from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any


RESULT_FIELDS = [
    "mylife_result_index",
    "mylife_full_name",
    "mylife_age",
    "mylife_birthday",
    "mylife_location",
    "mylife_former_names",
    "mylife_profile_url",
    "mylife_result_summary",
    "mylife_profile_summary",
    "mylife_query_strategy",
    "mylife_status",
    "mylife_message",
    "mylife_created_at",
]


class RealtimeCsvWriter:
    def __init__(self, output_dir: Path, input_path: Path, input_headers: list[str]) -> None:
        self.output_dir = output_dir
        self.input_path = input_path
        self.input_headers = input_headers
        self.headers = input_headers + RESULT_FIELDS
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self._select_path()
        self.file = self.path.open("a", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=self.headers, extrasaction="ignore")
        if self.path.stat().st_size == 0:
            self.writer.writeheader()
            self._flush()

    def _select_path(self) -> Path:
        base = self.output_dir / f"{self.input_path.stem}_MyLife结果.csv"
        if not base.exists() or base.stat().st_size == 0:
            return base
        with base.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = next(csv.reader(handle), [])
        if existing == self.headers:
            return base
        signature = hashlib.sha256("\0".join(self.headers).encode("utf-8")).hexdigest()[:8]
        return self.output_dir / f"{self.input_path.stem}_MyLife结果_{signature}.csv"

    def _flush(self) -> None:
        self.file.flush()
        os.fsync(self.file.fileno())

    def append(self, original: dict[str, str], result: dict[str, Any], created_at: str) -> None:
        row = {header: original.get(header, "") for header in self.input_headers}
        mapping = {
            "mylife_result_index": result.get("result_index", ""),
            "mylife_full_name": result.get("full_name", ""),
            "mylife_age": result.get("age", ""),
            "mylife_birthday": result.get("birthday", ""),
            "mylife_location": result.get("location", ""),
            "mylife_former_names": result.get("former_names", ""),
            "mylife_profile_url": result.get("profile_url", ""),
            "mylife_result_summary": result.get("result_summary", ""),
            "mylife_profile_summary": result.get("profile_summary", ""),
            "mylife_query_strategy": result.get("query_strategy", ""),
            "mylife_status": result.get("status", ""),
            "mylife_message": result.get("message", ""),
            "mylife_created_at": created_at,
        }
        row.update(mapping)
        self.writer.writerow(row)
        self._flush()

    def close(self) -> None:
        if not self.file.closed:
            self._flush()
            self.file.close()

    def first_column_values(self) -> set[str]:
        if not self.path.exists():
            return set()
        first = self.input_headers[0]
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {str(row.get(first, "")).strip() for row in csv.DictReader(handle) if str(row.get(first, "")).strip()}
