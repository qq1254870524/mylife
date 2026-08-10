from __future__ import annotations

import csv
import os
from pathlib import Path

from input_loader import _encoding


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
    lines = raw.decode(encoding).splitlines()
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
