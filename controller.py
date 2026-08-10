from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from browser_worker import BrowserSession, Cancelled, CloudflareFailure
from database import DatabaseWriter, JobDatabase
from input_loader import load_people
from models import PersonInput, RunConfig
from output_writer import RealtimeCsvWriter
from proxy_pool import ProxyGeo, ProxyPool, ProxySpec, parse_proxy_line
from source_rewriter import remove_completed_rows

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, int | str]], None]


class AppController:
    def __init__(self, config: RunConfig, log: LogFn | None = None, progress: ProgressFn | None = None) -> None:
        self.config = config
        self.gui_log = log or (lambda _message: None)
        self.progress = progress or (lambda _state: None)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.worker_threads: list[threading.Thread] = []
        self.runtime_dir = self.config.output_dir / ".mylife_runtime"
        self.profile_root = self.runtime_dir / "profiles"
        self.database = JobDatabase(self.runtime_dir / "state.sqlite3")
        self.log_path = self.config.output_dir / "logs" / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
        self._log_lock = threading.Lock()
        self._completed = 0
        self._total = 0
        self.output_path: Path | None = None

    def _log(self, message: str) -> None:
        clean = str(message).replace("\r", " ").strip()
        stamped = f"[{datetime.now():%H:%M:%S}] {clean}"
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(stamped + "\n")
            self.gui_log(stamped)

    def _emit_progress(self, status: str) -> None:
        summary = self.database.summary(self.config.input_file)
        payload: dict[str, int | str] = {
            "status": status,
            "total": self._total,
            "completed": summary.get("done", 0),
            "failed": summary.get("failed", 0),
            "pending": summary.get("pending", 0) + summary.get("retry", 0) + summary.get("running", 0),
            "output": str(self.output_path or ""),
        }
        self.progress(payload)

    def start_async(self) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("任务已经在运行")
        self.thread = threading.Thread(target=self.run, name="mylife-controller", daemon=False)
        self.thread.start()

    def stop(self) -> None:
        self._log("收到停止指令，正在结束所有线程并清理浏览器缓存")
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self.thread:
            self.thread.join(timeout)

    def _validate(self) -> list[ProxySpec]:
        if not self.config.input_file.is_file():
            raise FileNotFoundError(f"输入文件不存在：{self.config.input_file}")
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        if self.config.thread_count < 1:
            raise ValueError("线程数量至少为 1")
        if self.config.browser_mode not in {"小窗口", "无头"}:
            raise ValueError("浏览器模式只能是小窗口或无头")
        specs: list[ProxySpec] = []
        if self.config.proxy_enabled:
            specs = [parse_proxy_line(line, index) for index, line in enumerate(self.config.proxy_lines, 1) if line.strip()]
            if not specs:
                raise ValueError("勾选代理池后至少需要一套代理")
            if self.config.thread_count > len(specs):
                raise ValueError("线程数量不可以超过代理数量")
        return specs

    def _cleanup_cache(self) -> None:
        if self.profile_root.exists():
            shutil.rmtree(self.profile_root, ignore_errors=True)
        for path in self.runtime_dir.glob("*.tmp") if self.runtime_dir.exists() else ():
            try:
                path.unlink()
            except OSError:
                pass
        for pattern in (".mylife_rebuild.tmp", ".mylife_rebuild.xlsx", ".mylife_rebuild.xlsm"):
            for path in self.config.input_file.parent.glob(f".*{pattern}"):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _worker(
        self,
        worker_number: int,
        job_queue: queue.Queue[tuple[int, PersonInput, int]],
        writer: DatabaseWriter,
        proxy_spec: ProxySpec | None,
        proxy_pool: ProxyPool | None,
    ) -> None:
        session: BrowserSession | None = None
        challenge_streak = 0
        try:
            geo = ProxyGeo()
            if proxy_spec and proxy_pool:
                ready = proxy_pool.ensure_ready(proxy_spec, self.stop_event)
                if not ready:
                    self._log(f"{proxy_spec.label} 启动前检查未通过，本线程不启动浏览器")
                    return
                geo = ready
            session = BrowserSession(
                worker_number=worker_number,
                profile_dir=self.profile_root / f"worker-{worker_number}",
                mode=self.config.browser_mode,
                proxy_spec=proxy_spec,
                proxy_geo=geo,
                stop_event=self.stop_event,
                log=self._log,
                max_search_pages=self.config.max_search_pages,
            )
            while not self.stop_event.is_set():
                try:
                    job_id, person, attempts = job_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    writer.put("running", job_id)
                    if not session.page:
                        session.start(fresh_profile=True)
                    results = session.process(person)
                    for result in results:
                        writer.put("result", job_id, person, result)
                    writer.put("done", job_id, f"输出 {len(results)} 条")
                    challenge_streak = 0
                    self._log(f"线程{worker_number} 完成输入第 {person.source_row} 行，输出 {len(results)} 条")
                    session.clear_person_data()
                except Cancelled:
                    writer.put("retry", job_id, "用户停止")
                    break
                except CloudflareFailure as exc:
                    challenge_streak += 1
                    next_attempt = attempts + 1
                    self._log(f"线程{worker_number} Cloudflare 连续失败 {challenge_streak} 次")
                    if proxy_spec and proxy_pool and challenge_streak >= 2:
                        refreshed_geo = proxy_pool.refresh_after_challenge(proxy_spec, self.stop_event)
                        if not self.stop_event.is_set():
                            session.rebuild(refreshed_geo)
                            if refreshed_geo:
                                challenge_streak = 0
                    elif not self.stop_event.is_set():
                        session.rebuild()
                    if next_attempt < self.config.max_job_attempts and not self.stop_event.is_set():
                        writer.put("retry", job_id, str(exc))
                        job_queue.put((job_id, person, next_attempt))
                    else:
                        writer.put("failed", job_id, str(exc))
                except Exception as exc:
                    next_attempt = attempts + 1
                    message = f"{type(exc).__name__}: {exc}"
                    self._log(f"线程{worker_number} 第 {person.source_row} 行处理错误：{message}")
                    if next_attempt < self.config.max_job_attempts and not self.stop_event.is_set():
                        writer.put("retry", job_id, message)
                        job_queue.put((job_id, person, next_attempt))
                        if session:
                            session.rebuild()
                    else:
                        writer.put("failed", job_id, message)
                finally:
                    job_queue.task_done()
                    self._emit_progress("运行中" if not self.stop_event.is_set() else "正在停止")
        finally:
            if session:
                session.close()

    def run(self) -> None:
        writer: DatabaseWriter | None = None
        try:
            specs = self._validate()
            self._cleanup_cache()
            headers, people = load_people(self.config.input_file)
            self.database.reset_interrupted()
            inserted = self.database.import_people(people)
            jobs = self.database.pending_people(self.config.input_file, self.config.max_job_attempts)
            self._total = len(people)
            self._log(f"导入识别 {len(people)} 行，新增数据库任务 {inserted} 条，待处理 {len(jobs)} 条")
            csv_writer = RealtimeCsvWriter(self.config.output_dir, self.config.input_file, headers)
            self.output_path = csv_writer.path
            writer = DatabaseWriter(self.database, csv_writer, self._log)
            writer.start()
            job_queue: queue.Queue[tuple[int, PersonInput, int]] = queue.Queue()
            for job in jobs:
                job_queue.put(job)
            proxy_pool = ProxyPool(specs, self.runtime_dir / "proxy_refresh.json", self._log) if specs else None
            self.worker_threads = []
            for index in range(self.config.thread_count):
                spec = specs[index] if specs else None
                thread = threading.Thread(
                    target=self._worker,
                    args=(index + 1, job_queue, writer, spec, proxy_pool),
                    name=f"browser-worker-{index + 1}",
                    daemon=False,
                )
                self.worker_threads.append(thread)
                thread.start()
            for thread in self.worker_threads:
                thread.join()
            writer.flush()
            if self.stop_event.is_set():
                self._log("任务已停止；未执行输入文件重建，已保留数据库断点")
                self._emit_progress("已停止")
            else:
                summary = self.database.summary(self.config.input_file)
                unfinished = summary.get("pending", 0) + summary.get("retry", 0) + summary.get("running", 0)
                if unfinished:
                    self._log(f"仍有 {unfinished} 条任务未被可用浏览器领取；未重建输入文件")
                    self._emit_progress("失败")
                else:
                    completed = csv_writer.first_column_values()
                    removed = remove_completed_rows(self.config.input_file, completed)
                    self._log(f"全部处理线程结束，已一次性重建输入文件并删除 {removed} 行明确结果")
                    self._emit_progress("已完成" if not summary.get("failed", 0) else "失败")
        except Exception as exc:
            self._log(f"任务终止：{type(exc).__name__}: {exc}")
            self._emit_progress("失败")
        finally:
            if writer:
                try:
                    writer.close()
                except Exception as exc:
                    self._log(f"关闭数据库写入线程失败：{type(exc).__name__}: {exc}")
            self._cleanup_cache()
            self.worker_threads.clear()
