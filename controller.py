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
from proxy_pool import ProxyGeo, ProxyPool, ProxySpec, cleanup_profile_directory, parse_proxy_line
from runtime_monitor import RuntimeHealthMonitor
from source_rewriter import RealtimeInputRewriter

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
            "ERR_SSL_PROTOCOL_ERROR",
            "ERR_TIMED_OUT",
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
        self.input_rewriter = RealtimeInputRewriter(self.config.input_file)
        self._defer_input_rewrite = self.config.input_file.suffix.lower() in {".xlsx", ".xlsm"}

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
        completed = summary.get("done", 0)
        failed = summary.get("failed", 0)
        pending = summary.get("pending", 0) + summary.get("retry", 0) + summary.get("running", 0)
        # 完整成功批次会先清除 SQLite 断点，再发布 GUI 终态。此时数据库汇总已为 0，
        # 但界面必须保留本批次真实完成总数，不能出现“已完成 / 完成 0 / CSV 96”。
        if status == "已完成" and self._finished_clean:
            completed = self._total
            failed = 0
            pending = 0
        payload: dict[str, int | str] = {
            "status": status,
            "total": self._total,
            "completed": completed,
            "failed": failed,
            "pending": pending,
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
        cleanup_profile_directory(self.profile_root)
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
                    # 队列暂时为空不代表本轮已经结束：其他线程仍在处理的任务可能因
                    # Cloudflare/代理瞬断重新入队。worker 必须保持存活，直到所有
                    # 已领取任务也完成（unfinished_tasks == 0），否则四线程会在队尾
                    # 逐步退化成三、二、一个线程，最后的重试只能串行执行。
                    job_id, person, attempts = job_queue.get(timeout=0.5)
                except queue.Empty:
                    if job_queue.unfinished_tasks == 0:
                        break
                    continue
                try:
                    writer.put("running", job_id)
                    if not session.page:
                        session.start(fresh_profile=True)
                    results = session.process(person)
                    # 结果、完成状态和实时 CSV 先由唯一写线程同步落盘；确认后才删输入行。
                    writer.put_sync("complete", job_id, person, tuple(results), f"输出 {len(results)} 条")
                    challenge_streak = 0
                    if self._defer_input_rewrite:
                        self._log(
                            f"线程{worker_number} 完成输入原第 {person.source_row} 行，输出 {len(results)} 条；"
                            "XLSX/XLSM 处理中不删行，等待全部线程结束后一次性删除"
                        )
                    else:
                        removed = self.input_rewriter.remove_person(person)
                        self._log(
                            f"线程{worker_number} 完成输入原第 {person.source_row} 行，输出 {len(results)} 条；"
                            f"明确结果已实时删除输入行 {removed} 条"
                        )
                    session.clear_person_data()
                except Cancelled:
                    writer.put("retry_cancelled", job_id, "用户停止")
                    break
                except CloudflareFailure as exc:
                    if self.stop_event.is_set():
                        # 停止信号可能在 Cloudflare 模块返回失败前到达；这仍是可续跑任务，
                        # 不能因为异常包装形式不同而被记成终态失败。
                        writer.put("retry_cancelled", job_id, "用户停止")
                        break
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
                    if self.stop_event.is_set():
                        # 页面导航、详情采集或 challenge 内层可能把取消包装成 RuntimeError。
                        # 只要控制器已进入停止流程，当前任务统一退回 retry 并立即结束线程。
                        writer.put("retry_cancelled", job_id, "用户停止")
                        self._log(
                            f"线程{worker_number} 第 {person.source_row} 行在停止期间取消，"
                            "任务已保留为可续跑状态"
                        )
                        break
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
            existing_jobs = self.database.source_job_count(self.config.input_file)
            # 实时删行会改变当前文件行号；有断点时以 SQLite 保存的原始任务为准，禁止按
            # 缩短后的新行号重复导入或覆盖另一人的任务。
            inserted = 0 if existing_jobs else self.database.import_people(people)
            failed_requeued = self.database.reset_failed_for_new_run(self.config.input_file)
            exhausted_requeued = self.database.reset_exhausted_incomplete_for_new_run(
                self.config.input_file, self.config.max_job_attempts
            )
            requeued = self.database.reset_incomplete_demographics(self.config.input_file, SEARCH_REVISION)
            jobs = self.database.pending_people(self.config.input_file, self.config.max_job_attempts)
            self._total = self.database.source_job_count(self.config.input_file)
            self._log(
                f"当前输入识别 {len(people)} 行，断点任务 {existing_jobs} 条，"
                f"新增数据库任务 {inserted} 条，待处理 {len(jobs)} 条"
            )
            if requeued:
                self._log(
                    f"搜索/字段策略升级：重试 {requeued} 条旧版生日、性别或星座不完整结果，"
                    "并从 SQLite 重建实时 CSV"
                )
            if failed_requeued:
                self._log(f"新一轮启动：重新排队 {failed_requeued} 条上一轮技术失败行")
            if exhausted_requeued:
                self._log(
                    f"新一轮启动：修复并重新排队 {exhausted_requeued} 条旧停止流程遗留的不可领取行"
                )
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
            if not self._defer_input_rewrite:
                catchup_removed = self.input_rewriter.remove_people(self.database.done_people(self.config.input_file))
                if catchup_removed:
                    self._log(f"启动断点核对：已补删 {catchup_removed} 条此前已有明确结果的输入行")
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
            # 线程刚启动就推送当前输入文件的独立汇总，不等待第一条耗时任务完成。
            self.monitor.write_once()
            self._emit_progress("运行中")
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
                if self._defer_input_rewrite:
                    # “停止”同样先等待全部 worker join 并关闭数据库写线程；此时才对
                    # XLSX/XLSM 执行本轮唯一一次批量删行，未完成任务仍保留在断点库。
                    stopped_removed = self.input_rewriter.remove_people(
                        self.database.done_people(self.config.input_file)
                    )
                    self._log(
                        f"任务已停止；全部线程已结束，已一次性重建 XLSX/XLSM 并删除 "
                        f"{stopped_removed} 条明确结果；未完成行和数据库断点已保留"
                    )
                else:
                    self._log(
                        "任务已停止；CSV/TXT 的明确结果行已在提交后实时删除；"
                        "未完成行和数据库断点已保留"
                    )
                self._final_status = "已停止"
            else:
                summary = self.database.summary(self.config.input_file)
                unfinished = summary.get("pending", 0) + summary.get("retry", 0) + summary.get("running", 0)
                if unfinished:
                    self._log(f"仍有 {unfinished} 条任务未被可用浏览器领取；未重建输入文件")
                    self._final_status = "失败"
                else:
                    failed = summary.get("failed", 0)
                    deferred_removed = 0
                    if self._defer_input_rewrite:
                        # Excel 工作簿在处理过程中始终保持行号和结构不变；全部 worker join、
                        # SQLite/CSV 写线程关闭后才打开一次工作簿并批量删掉所有明确结果行。
                        deferred_removed = self.input_rewriter.remove_people(
                            self.database.done_people(self.config.input_file)
                        )
                    self._finished_clean = failed == 0
                    self._final_status = "已完成" if self._finished_clean else "失败"
                    purged = self.database.delete_source(self.config.input_file)
                    if self._defer_input_rewrite:
                        self._log(
                            f"全部处理线程结束后已一次性重建 XLSX/XLSM，并删除 {deferred_removed} 条明确结果；"
                            f"清理断点任务 {purged} 条；失败保留 {failed} 行"
                        )
                    else:
                        self._log(
                            "全部处理线程结束；每条明确结果均已在 SQLite/CSV 提交后实时删除输入行；"
                            f"清理断点任务 {purged} 条；失败保留 {failed} 行"
                        )
        except Exception as exc:
            self._log(f"任务终止：{type(exc).__name__}: {exc}")
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
            # GUI 只有在 profiles/临时文件清理真正返回后才允许再次点击“开始”，
            # 避免上一批 Chromium 子进程仍在退出时新一轮复用旧缓存目录。
            self._emit_progress(self._final_status)
            if self._finished_clean:
                shutil.rmtree(self.runtime_dir, ignore_errors=True)
            self.worker_threads.clear()
