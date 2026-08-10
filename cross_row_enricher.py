from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from demographics_enricher import zodiac_from_birthday


PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.()-]*)?(\d{3})[\s.()-]*(\d{3})[\s.-]*(\d{4})(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def _key(value: object) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "").strip().lower())


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _value(original: dict[str, Any], keys: set[str]) -> str:
    for header, value in original.items():
        if _key(header) in keys and str(value or "").strip():
            return str(value).strip()
    return ""


def _name(original: dict[str, Any]) -> str:
    full = _value(original, {"fullname", "name", "姓名", "完整姓名"})
    if full:
        return _compact(full)
    return _compact(
        " ".join(
            _value(original, keys)
            for keys in ({"firstname", "first", "名"}, {"middlename", "middle", "中间名"}, {"lastname", "last", "姓"})
        )
    )


def _signals(original: dict[str, Any]) -> set[str]:
    name = _name(original)
    if not name:
        return set()
    signals: set[str] = set()
    address = _value(original, {"currentaddress", "address", "当前地址", "地址"})
    if len(_compact(address)) >= 12:
        signals.add(f"A:{name}:{_compact(address)}")
    phone_texts = [
        _value(original, {"phonenumbers", "phones", "phone", "primaryphone", "手机号", "电话"})
    ]
    search_type = _value(original, {"searchtype", "搜索类型", "查询类型"}).lower()
    if any(marker in search_type for marker in ("phone", "mobile", "电话", "手机")):
        phone_texts.append(_value(original, {"query", "查询", "搜索值"}))
    for text in phone_texts:
        for match in PHONE_RE.finditer(text):
            signals.add(f"P:{name}:{''.join(match.groups())}")
    email_text = _value(original, {"emailaddresses", "emails", "email", "邮箱"})
    for match in EMAIL_RE.finditer(email_text):
        signals.add(f"E:{name}:{match.group(0).lower()}")
    return signals


def enrich_cross_rows(
    records: list[tuple[int, dict[str, Any], dict[str, Any], str]],
) -> dict[int, dict[str, Any]]:
    """按姓名+完整电话/邮箱/当前地址聚类，只用无冲突字段补同一人的其他输入行。"""

    parent = list(range(len(records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    owners: dict[str, int] = {}
    for index, (_, original, _, _) in enumerate(records):
        for signal in _signals(original):
            if signal in owners:
                union(index, owners[signal])
            else:
                owners[signal] = index

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(records)):
        groups[find(index)].append(index)

    updates: dict[int, dict[str, Any]] = {}
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        unique: dict[str, set[str]] = {
            field: {
                str(records[index][2].get(field) or "").strip()
                for index in indexes
                if str(records[index][2].get(field) or "").strip()
            }
            for field in ("birthday", "gender", "zodiac")
        }
        canonical = {field: next(iter(values)) for field, values in unique.items() if len(values) == 1}
        if canonical.get("birthday") and not canonical.get("zodiac"):
            zodiac = zodiac_from_birthday(canonical["birthday"])
            if zodiac:
                canonical["zodiac"] = zodiac
        for index in indexes:
            result_id, _, original_result, _ = records[index]
            result = dict(original_result)
            filled: list[str] = []
            for field, label in (("birthday", "生日"), ("gender", "性别"), ("zodiac", "星座")):
                if not str(result.get(field) or "").strip() and canonical.get(field):
                    result[field] = canonical[field]
                    filled.append(label)
            if not filled:
                continue
            note = str(result.get("demographics_note") or "").strip()
            extra = f"{'/'.join(filled)}=跨输入行强信号一致补充"
            result["demographics_note"] = "；".join(x for x in (note, extra) if x)
            if "生日" in filled:
                status = str(result.get("status") or "")
                if "生日未公开" in status:
                    result["status"] = status.replace("身份但生日未公开", "生日")
                elif status in {"无结果", "输入无效"}:
                    result["status"] = "已通过跨输入行强信号匹配生日（高置信度）"
                message = str(result.get("message") or "").strip()
                result["message"] = "；".join(x for x in (message, "同姓名且完整电话/邮箱/当前地址一致") if x)
            updates[result_id] = result
    return updates
