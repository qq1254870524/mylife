from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


RESULT_FIELDS = ["生日", "性别", "星座"]


class RealtimeCsvWriter:
    def __init__(self, output_dir: Path, input_path: Path, input_headers: list[str]) -> None:
        self.output_dir = output_dir
        self.input_path = input_path
        self.input_headers = input_headers
        self.headers = input_headers + RESULT_FIELDS
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.path = self._select_path()
        self._existing_counts = self._load_signatures()
        self._restore_seen: Counter[str] = Counter()
        self.file = self.path.open("a", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=self.headers, extrasaction="ignore")
        if self.path.stat().st_size == 0:
            self.writer.writeheader()
            self._flush()

    def _row(self, original: dict[str, str], result: dict[str, Any], created_at: str) -> dict[str, Any]:
        row: dict[str, Any] = {header: original.get(header, "") for header in self.input_headers}
        row["生日"] = result.get("birthday", "")
        row["性别"] = result.get("gender", "")
        row["星座"] = result.get("zodiac", "")
        return row

    def _signature(self, row: dict[str, Any]) -> str:
        payload = {key: row.get(key, "") for key in self.headers}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _load_signatures(self) -> Counter[str]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return Counter()
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return Counter(self._signature(dict(row)) for row in csv.DictReader(handle))

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

    def append(
        self,
        original: dict[str, str],
        result: dict[str, Any],
        created_at: str,
        *,
        restoring: bool = False,
    ) -> bool:
        row = self._row(original, result, created_at)
        signature = self._signature(row)
        if restoring:
            self._restore_seen[signature] += 1
            if self._restore_seen[signature] <= self._existing_counts[signature]:
                return False
        self.writer.writerow(row)
        self._flush()
        self._existing_counts[signature] += 1
        return True

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
