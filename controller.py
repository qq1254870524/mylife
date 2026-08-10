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
from cross_row_enricher import enrich_cross_rows
from database import DatabaseWriter, JobDatabase
from input_loader import load_people
from models import SEARCH_REVISION, PersonInput, RunConfig
from output_writer import RealtimeCsvWriter
from proxy_pool import ProxyGeo, ProxyPool, ProxySpec, parse_proxy_line
from runtime_monitor import RuntimeHealthMonitor
from source_rewriter import remove_completed_rows

LogFn = Callable[[str], None]
ProgressFn = Callable[[dict[str, int | str]], None]


def _is_proxy_network_error(message: str) -> bool:
    upper = str(message or "").upper()
    return any(
        marker in upper
        for marker in (
            "ERR_SOCKS_CONNECTION_FAILED",
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_TUNNEL_CONNECTION_FAILED",
            "SOCKS SERVER GENERAL FAILURE",
            "CONNECTION RESET BY PEER",
        )
    )


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
        self.monitor: RuntimeHealthMonitor | None = None
        self._finished_clean = False
        self.diagnostics_dir = self.runtime_dir / "diagnostics" / self.log_path.stem
        self._final_status = "未开始"

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
        if self.monitor:
            csv_audit = dict(self.monitor.snapshot().get("csv", {}))
            payload["output_rows"] = int(csv_audit.get("rows", 0))
            payload["birthdays"] = int(csv_audit.get("birthdays", 0))
            payload["genders"] = int(csv_audit.get("genders", 0))
            payload["zodiacs"] = int(csv_audit.get("zodiacs", 0))
            payload["remarks"] = int(csv_audit.get("remarks", 0))
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

    def _write_run_summary(self) -> None:
        payload = {
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": self._final_status,
            "input_file": str(self.config.input_file),
            "output_file": str(self.output_path or ""),
            "total": self._total,
            "monitor": self.monitor.snapshot() if self.monitor else {},
        }
        target = self.log_path.with_name(self.log_path.stem + "_summary.json")
        temporary = target.with_suffix(".json.tmp")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)

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
                ready = proxy_pool.wait_until_ready(proxy_spec, self.stop_event)
                if not ready:
                    self._log(f"{proxy_spec.label} 等待恢复期间收到停止指令，本线程正常结束")
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
                diagnostics_dir=self.diagnostics_dir,
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
                    if session:
                        session.capture_diagnostic("cloudflare-failure")
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
                    if session:
                        session.capture_diagnostic("job-error")
                    proxy_network_error = bool(
                        proxy_spec and proxy_pool and _is_proxy_network_error(message)
                    )
                    if proxy_network_error and not self.stop_event.is_set():
                        # 通道故障属于代理资源故障，不消耗人员任务的业务重试次数。
                        writer.put("retry_network", job_id, message)
                        job_queue.put((job_id, person, attempts))
                        self._log(
                            f"线程{worker_number} 检测到代理通道错误，关闭错误页并保持线程等待代理恢复"
                        )
                        session.close()
                        refreshed_geo = proxy_pool.wait_until_ready(proxy_spec, self.stop_event)
                        if refreshed_geo and not self.stop_event.is_set():
                            session.rebuild(refreshed_geo)
                            self._log(f"线程{worker_number} 代理恢复，已使用全新 profile 继续领取任务")
                    elif next_attempt < self.config.max_job_attempts and not self.stop_event.is_set():
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
            failed_requeued = self.database.reset_failed_for_new_run(self.config.input_file)
            requeued = self.database.reset_incomplete_demographics(self.config.input_file, SEARCH_REVISION)
            jobs = self.database.pending_people(self.config.input_file, self.config.max_job_attempts)
            self._total = len(people)
            self._log(f"导入识别 {len(people)} 行，新增数据库任务 {inserted} 条，待处理 {len(jobs)} 条")
            if requeued:
                self._log(
                    f"搜索/字段策略升级：重试 {requeued} 条旧版生日、性别或星座不完整结果，"
                    "并从 SQLite 重建实时 CSV"
                )
            if failed_requeued:
                self._log(f"新一轮启动：重新排队 {failed_requeued} 条上一轮技术失败行")
            csv_writer = RealtimeCsvWriter(
                self.config.output_dir,
                self.config.input_file,
                headers,
                rebuild=bool(requeued),
            )
            self.output_path = csv_writer.path
            restored = sum(
                1
                for original, result, created_at in self.database.existing_results(self.config.input_file)
                if csv_writer.append(original, result, created_at, restoring=True)
            )
            if restored:
                self._log(f"从 SQLite 断点恢复实时 CSV：补写 {restored} 行")
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
            self.monitor = RuntimeHealthMonitor(
                self.database,
                self.config.input_file,
                csv_writer.path,
                self.runtime_dir,
                self.log_path,
                lambda: list(self.worker_threads),
            )
            self.monitor.start()
            for thread in self.worker_threads:
                thread.join()
            if self.monitor:
                self.monitor.stop()
            writer.flush()
            writer.close()
            writer = None
            records = self.database.result_records(self.config.input_file)
            cross_row_updates = enrich_cross_rows(records)
            if cross_row_updates:
                updated = self.database.update_result_records(cross_row_updates)
                rebuilt_writer = RealtimeCsvWriter(
                    self.config.output_dir,
                    self.config.input_file,
                    headers,
                    rebuild=True,
                )
                for original, result, created_at in self.database.existing_results(self.config.input_file):
                    rebuilt_writer.append(original, result, created_at)
                rebuilt_writer.close()
                self._log(f"跨输入行强身份信号补充 {updated} 条结果，已从 SQLite 重建实时 CSV")
            if self.stop_event.is_set():
                self._log("任务已停止；未执行输入文件重建，已保留数据库断点")
                self._emit_progress("已停止")
                self._final_status = "已停止"
            else:
                summary = self.database.summary(self.config.input_file)
                unfinished = summary.get("pending", 0) + summary.get("retry", 0) + summary.get("running", 0)
                if unfinished:
                    self._log(f"仍有 {unfinished} 条任务未被可用浏览器领取；未重建输入文件")
                    self._emit_progress("失败")
                    self._final_status = "失败"
                else:
                    completed_rows = self.database.done_source_rows(self.config.input_file)
                    removed = remove_completed_rows(self.config.input_file, completed_rows)
                    failed = summary.get("failed", 0)
                    self._finished_clean = failed == 0
                    self._final_status = "已完成" if self._finished_clean else "失败"
                    self._emit_progress(self._final_status)
                    purged = self.database.delete_source(self.config.input_file)
                    self._log(
                        f"全部处理线程结束，已按源行号一次性重建输入文件并删除 {removed} 行明确结果；"
                        f"清理断点任务 {purged} 条；失败保留 {failed} 行"
                    )
        except Exception as exc:
            self._log(f"任务终止：{type(exc).__name__}: {exc}")
            self._emit_progress("失败")
            self._final_status = "失败"
        finally:
            if self.monitor:
                try:
                    self.monitor.stop()
                except Exception:
                    pass
            if writer:
                try:
                    writer.close()
                except Exception as exc:
                    self._log(f"关闭数据库写入线程失败：{type(exc).__name__}: {exc}")
            try:
                self._write_run_summary()
            except Exception as exc:
                self._log(f"写入运行汇总失败：{type(exc).__name__}: {exc}")
            self._cleanup_cache()
            if self._finished_clean:
                shutil.rmtree(self.runtime_dir, ignore_errors=True)
            self.worker_threads.clear()
