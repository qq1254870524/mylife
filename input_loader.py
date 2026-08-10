from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from models import PersonInput


ALIASES: dict[str, tuple[str, ...]] = {
    "first_name": ("firstname", "first_name", "first name", "名", "名字", "姓名名"),
    "middle_name": ("middlename", "middle_name", "middle name", "中间名"),
    "last_name": ("lastname", "last_name", "last name", "姓", "姓氏"),
    "full_name": ("fullname", "full_name", "full name", "name", "姓名", "客户姓名"),
    "city": ("city", "城市", "town"),
    "state": ("state", "州", "省", "province"),
    "zip_code": ("zip", "zipcode", "zip_code", "zip code", "postalcode", "邮编"),
    "location": ("location", "address", "地址", "地区", "citystatezip"),
    "query": ("query", "search", "keyword", "关键词", "查询", "搜索资料"),
}


def _key(value: object) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _find_header(headers: list[str], logical: str) -> str | None:
    wanted = {_key(x) for x in ALIASES[logical]}
    return next((h for h in headers if _key(h) in wanted), None)


def _unique_headers(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    used: dict[str, int] = {}
    for index, raw in enumerate(values, 1):
        base = str(raw or "").strip() or f"column_{index}"
        count = used.get(base, 0) + 1
        used[base] = count
        result.append(base if count == 1 else f"{base}_{count}")
    return result


def _encoding(raw: bytes) -> str:
    for name in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            raw.decode(name)
            return name
        except UnicodeDecodeError:
            continue
    return "utf-8"


def _read_delimited(path: Path) -> tuple[list[str], list[list[object]]]:
    raw = path.read_bytes()
    text = raw.decode(_encoding(raw))
    sample = text[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(text.splitlines(), dialect))
    if not rows:
        return ["query"], []
    has_header = False
    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        pass
    if path.suffix.lower() == ".csv" or has_header:
        return _unique_headers(rows[0]), rows[1:]
    return [f"column_{i + 1}" for i in range(max(map(len, rows)))], rows


def _read_txt(path: Path) -> tuple[list[str], list[list[object]]]:
    raw = path.read_bytes()
    text = raw.decode(_encoding(raw))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ["query"], []
    if any(delimiter in lines[0] for delimiter in (",", "\t", ";", "|")):
        return _read_delimited(path)
    return ["query"], [[line] for line in lines]


def _read_xlsx(path: Path) -> tuple[list[str], list[list[object]]]:
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        iterator = sheet.iter_rows(values_only=True)
        first = next(iterator, None)
        if first is None:
            return ["query"], []
        return _unique_headers(first), [list(row) for row in iterator]
    finally:
        workbook.close()


def read_rows(path: str | Path) -> tuple[list[str], list[list[object]]]:
    source = Path(path).resolve()
    suffix = source.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return _read_xlsx(source)
    if suffix == ".csv":
        return _read_delimited(source)
    if suffix == ".txt":
        return _read_txt(source)
    raise ValueError(f"不支持的输入文件类型：{suffix or '无扩展名'}")


def _value(row: dict[str, str], header: str | None) -> str:
    return str(row.get(header, "") if header else "").strip()


def _parse_name(name: str) -> tuple[str, str, str]:
    clean = re.sub(r"\s+", " ", name.replace(",", " ")).strip()
    parts = clean.split()
    if len(parts) < 2:
        return (parts[0] if parts else ""), "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _parse_location(location: str) -> tuple[str, str, str]:
    clean = re.sub(r"\s+", " ", location).strip(" ,")
    zip_match = re.search(r"\b(\d{5}(?:-\d{4})?)\b", clean)
    zip_code = zip_match.group(1) if zip_match else ""
    if zip_match:
        clean = (clean[: zip_match.start()] + clean[zip_match.end() :]).strip(" ,")
    state_match = re.search(r"(?:,|\s)\s*([A-Za-z]{2})$", clean)
    state = state_match.group(1).upper() if state_match else ""
    city = clean[: state_match.start()].strip(" ,") if state_match else clean
    return city, state, zip_code


def _looks_unsupported_query(value: str) -> bool:
    compact = re.sub(r"[\s()+.\-]", "", value)
    return "@" in value or (compact.isdigit() and len(compact) >= 7)


def load_people(path: str | Path) -> tuple[list[str], list[PersonInput]]:
    source = Path(path).resolve()
    headers, raw_rows = read_rows(source)
    first_header = headers[0]
    mapped = {logical: _find_header(headers, logical) for logical in ALIASES}
    people: list[PersonInput] = []
    for source_row, raw in enumerate(raw_rows, 2):
        values = ["" if value is None else str(value).strip() for value in raw]
        values.extend([""] * max(0, len(headers) - len(values)))
        row = dict(zip(headers, values))
        if not any(row.values()):
            continue
        first_value = row.get(first_header, "").strip()
        first_name = _value(row, mapped["first_name"])
        middle_name = _value(row, mapped["middle_name"])
        last_name = _value(row, mapped["last_name"])
        full_name = _value(row, mapped["full_name"])
        query = _value(row, mapped["query"]) or first_value
        if not first_name or not last_name:
            guessed = _parse_name(full_name or query)
            first_name = first_name or guessed[0]
            middle_name = middle_name or guessed[1]
            last_name = last_name or guessed[2]
        city = _value(row, mapped["city"])
        state = _value(row, mapped["state"]).upper()
        zip_code = _value(row, mapped["zip_code"])
        location = _value(row, mapped["location"])
        if location and (not city or not state or not zip_code):
            guessed_city, guessed_state, guessed_zip = _parse_location(location)
            city = city or guessed_city
            state = state or guessed_state
            zip_code = zip_code or guessed_zip
        validation_error = ""
        if _looks_unsupported_query(query) and not (mapped["first_name"] and mapped["last_name"]):
            validation_error = "输入中没有可识别的姓名字段"
        elif not first_name or not last_name:
            validation_error = "姓名至少需要名和姓"
        people.append(
            PersonInput(
                source_path=source,
                source_row=source_row,
                headers=headers,
                original=row,
                first_value=first_value,
                first_name=first_name,
                middle_name=middle_name,
                last_name=last_name,
                city=city,
                state=state,
                zip_code=zip_code,
                validation_error=validation_error,
            )
        )
    return headers, people
