from __future__ import annotations

import csv
import os
import threading
import time
from pathlib import Path
from typing import Iterable, Mapping

from input_loader import _encoding, load_people
from models import PersonInput


def _rewrite_csv(path: Path, completed_rows: set[int]) -> int:
    raw = path.read_bytes()
    encoding = _encoding(raw)
    text = raw.decode(encoding)
    try:
        dialect = csv.Sniffer().sniff(text[:65536], delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return 0
    kept = [rows[0]]
    removed = 0
    for source_row, row in enumerate(rows[1:], 2):
        if source_row in completed_rows:
            removed += 1
        else:
            kept.append(row)
    temp = path.with_name(f".{path.name}.mylife_rebuild.tmp")
    with temp.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, dialect)
        writer.writerows(kept)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return removed


def _rewrite_txt(path: Path, completed_rows: set[int]) -> int:
    raw = path.read_bytes()
    encoding = _encoding(raw)
    text = raw.decode(encoding)
    lines = text.splitlines()
    first_nonblank = next((line for line in lines if line.strip()), "")
    if any(delimiter in first_nonblank for delimiter in (",", "\t", ";", "|")):
        sample = text[:65536]
        try:
            has_header = csv.Sniffer().has_header(sample)
        except csv.Error:
            has_header = False
        removed = 0
        kept: list[str] = []
        for physical_index, line in enumerate(lines):
            # _read_delimited: 有表头时第一条数据源行为 2；无表头时第一行也从 2 开始。
            source_row = physical_index + (1 if has_header else 2)
            if (not has_header or physical_index > 0) and source_row in completed_rows:
                removed += 1
            else:
                kept.append(line)
        temp = path.with_name(f".{path.name}.mylife_rebuild.tmp")
        with temp.open("w", encoding=encoding, newline="") as handle:
            handle.write("\n".join(kept) + ("\n" if kept else ""))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        return removed

    removed = 0
    kept: list[str] = []
    logical_source_row = 2
    for line in lines:
        if not line.strip():
            kept.append(line)
            continue
        if logical_source_row in completed_rows:
            removed += 1
        else:
            kept.append(line)
        logical_source_row += 1
    temp = path.with_name(f".{path.name}.mylife_rebuild.tmp")
    with temp.open("w", encoding=encoding, newline="") as handle:
        handle.write("\n".join(kept) + ("\n" if kept else ""))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return removed


def _rewrite_xlsx(path: Path, completed_rows: set[int]) -> int:
    import openpyxl

    keep_vba = path.suffix.lower() == ".xlsm"
    workbook = openpyxl.load_workbook(path, keep_vba=keep_vba)
    removed = 0
    try:
        sheet = workbook.active
        delete_rows = sorted(
            (row_index for row_index in completed_rows if 2 <= row_index <= sheet.max_row),
            reverse=True,
        )
        for row_index in delete_rows:
            sheet.delete_rows(row_index, 1)
        removed = len(delete_rows)
        temp = path.with_name(f".{path.stem}.mylife_rebuild{path.suffix}")
        workbook.save(temp)
    finally:
        workbook.close()
    os.replace(temp, path)
    return removed


def remove_completed_rows(path: str | Path, completed_source_rows: set[int]) -> int:
    """仅在整批结束后按原始源行号重建，重复首列值不会误删未完成行。"""

    source = Path(path).resolve()
    completed_rows = {int(value) for value in completed_source_rows if int(value) >= 2}
    if not completed_rows:
        return 0
    suffix = source.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _rewrite_xlsx(source, completed_rows)
    if suffix == ".csv":
        return _rewrite_csv(source, completed_rows)
    if suffix == ".txt":
        return _rewrite_txt(source, completed_rows)
    raise ValueError(f"不支持重建的文件类型：{suffix}")


def _original_key(original: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """与 input_loader 相同地按去首尾空白后的完整原始行匹配。"""

    return tuple(sorted((str(key), str(value or "").strip()) for key, value in original.items()))


class RealtimeInputRewriter:
    """在明确结果持久化后，从当前输入中原子删除对应的一条完整记录。

    数据库保留最初导入的 PersonInput；文件删行后行号会变化，因此实时阶段不能再用
    最初的 source_row。这里每次按完整原始行重新定位当前行号，合法重复行也只按完成
    数量逐条删除。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._lock = threading.Lock()

    def remove_person(self, person: PersonInput) -> int:
        return self.remove_originals([person.original])

    def remove_people(self, people: Iterable[PersonInput]) -> int:
        return self.remove_originals(person.original for person in people)

    def remove_originals(self, originals: Iterable[Mapping[str, object]]) -> int:
        wanted: dict[tuple[tuple[str, str], ...], int] = {}
        for original in originals:
            key = _original_key(original)
            wanted[key] = wanted.get(key, 0) + 1
        if not wanted:
            return 0

        with self._lock:
            _headers, current_people = load_people(self.path)
            current_rows: set[int] = set()
            for current in current_people:
                key = _original_key(current.original)
                remaining = wanted.get(key, 0)
                if remaining <= 0:
                    continue
                current_rows.add(current.source_row)
                wanted[key] = remaining - 1
            if not current_rows:
                return 0

            # Windows 上杀毒/索引器可能短暂占用文件；同一锁内重试，避免并发临时文件冲突。
            last_error: OSError | None = None
            for attempt in range(5):
                try:
                    return remove_completed_rows(self.path, current_rows)
                except OSError as exc:
                    last_error = exc
                    if attempt == 4:
                        raise
                    time.sleep(0.2 * (attempt + 1))
            if last_error:
                raise last_error
            return 0
