from __future__ import annotations

import csv
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from database import JobDatabase


class RuntimeHealthMonitor(threading.Thread):
    """只读审计数据库、CSV、线程和日志，并原子更新实时健康快照。"""

    def __init__(
        self,
        database: JobDatabase,
        source_path: Path,
        output_path: Path,
        runtime_dir: Path,
        log_path: Path,
        worker_threads: Callable[[], list[threading.Thread]],
        interval: float = 10.0,
    ) -> None:
        super().__init__(name="runtime-health-monitor", daemon=False)
        self.database = database
        self.source_path = source_path
        self.output_path = output_path
        self.runtime_dir = runtime_dir
        self.log_path = log_path
        self.worker_threads = worker_threads
        self.interval = interval
        self.stop_event = threading.Event()
        self.latest: dict[str, object] = {}
        self._lock = threading.Lock()

    @staticmethod
    def audit_csv(path: Path) -> dict[str, object]:
        audit: dict[str, object] = {
            "exists": path.is_file(),
            "rows": 0,
            "birthdays": 0,
            "unique_birthdays": 0,
            "blank_birthdays": 0,
            "genders": 0,
            "zodiacs": 0,
            "remarks": 0,
            "bytes": path.stat().st_size if path.is_file() else 0,
        }
        if not path.is_file() or path.stat().st_size == 0:
            return audit
        unique_birthdays: set[str] = set()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                audit["rows"] = int(audit["rows"]) + 1
                birthday = str(row.get("生日", "")).strip()
                if birthday:
                    audit["birthdays"] = int(audit["birthdays"]) + 1
                    unique_birthdays.add(birthday)
                else:
                    audit["blank_birthdays"] = int(audit["blank_birthdays"]) + 1
                if str(row.get("性别", "")).strip():
                    audit["genders"] = int(audit["genders"]) + 1
                if str(row.get("星座", "")).strip():
                    audit["zodiacs"] = int(audit["zodiacs"]) + 1
                if str(row.get("备注原因", "")).strip():
                    audit["remarks"] = int(audit["remarks"]) + 1
        audit["unique_birthdays"] = len(unique_birthdays)
        return audit

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return dict(self.latest)

    def write_once(self) -> dict[str, object]:
        threads = self.worker_threads()
        payload: dict[str, object] = {
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "database": self.database.summary(self.source_path),
            "csv": self.audit_csv(self.output_path),
            "threads": {
                "total": len(threads),
                "alive": sum(1 for thread in threads if thread.is_alive()),
                "names": [thread.name for thread in threads if thread.is_alive()],
            },
            "log": {
                "path": str(self.log_path),
                "bytes": self.log_path.stat().st_size if self.log_path.is_file() else 0,
            },
        }
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        target = self.runtime_dir / "health.json"
        temporary = self.runtime_dir / "health.json.tmp"
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        with self._lock:
            self.latest = payload
        return payload

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.write_once()
            except Exception:
                pass
            self.stop_event.wait(self.interval)
        try:
            self.write_once()
        except Exception:
            pass

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=max(5.0, self.interval + 2.0))
