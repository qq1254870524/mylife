from __future__ import annotations

import re

from models import PersonInput, SearchResult


FORMER_NAME_KEYS = {
    "formername",
    "formernames",
    "alias",
    "aliases",
    "othernames",
    "曾用名",
    "别名",
}
NON_NAME_WORDS = {
    "address",
    "age",
    "and",
    "city",
    "county",
    "currently",
    "former",
    "formerly",
    "lived",
    "lives",
    "located",
    "location",
    "resided",
    "state",
}


def _key(value: object) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "").strip().lower())


def _age(value: object) -> int | None:
    match = re.search(r"\b(\d{1,3})\b", str(value or ""))
    if not match:
        return None
    number = int(match.group(1))
    return number if 0 < number < 125 else None


def has_exact_age(results: list[SearchResult], input_age: str) -> bool:
    expected = _age(input_age)
    return bool(expected is not None and any(_age(result.age) == expected for result in results))


def merge_search_results(target: list[SearchResult], additions: list[SearchResult]) -> int:
    seen = {item.profile_url for item in target}
    added = 0
    for item in additions:
        if item.profile_url and item.profile_url not in seen:
            target.append(item)
            seen.add(item.profile_url)
            added += 1
    return added


def former_name_pairs(person: PersonInput, limit: int = 3) -> list[tuple[str, str]]:
    """只保留与主姓名共享名/姓的干净曾用名，过滤地址和解析噪声。"""

    primary_first = person.first_name.strip().lower()
    primary_last = person.last_name.strip().lower()
    primary = (primary_first, primary_last)
    pairs: list[tuple[str, str]] = []
    seen = {primary}
    for header, raw in person.original.items():
        if _key(header) not in FORMER_NAME_KEYS:
            continue
        for part in re.split(r"[|;\r\n]+", str(raw or "")):
            words = re.findall(r"[A-Za-z][A-Za-z'.-]*", part)
            lowered = [word.lower().strip(".'-") for word in words]
            if not 2 <= len(words) <= 4 or any(word in NON_NAME_WORDS for word in lowered):
                continue
            first, last = words[0], words[-1]
            pair = (first.lower(), last.lower())
            if pair in seen or (primary_first not in pair and primary_last not in pair):
                continue
            seen.add(pair)
            pairs.append((first, last))
            if len(pairs) >= limit:
                return pairs
    return pairs
