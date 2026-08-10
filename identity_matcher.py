from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher

from models import PersonInput, ProfileResult


PHONE_KEYS = {
    "phone",
    "phones",
    "phonenumber",
    "phonenumbers",
    "primaryphone",
    "mobile",
    "telephone",
    "电话",
    "手机号",
    "手机号码",
}
EMAIL_KEYS = {"email", "emails", "emailaddress", "emailaddresses", "邮箱", "电子邮箱"}
CURRENT_ADDRESS_KEYS = {
    "address",
    "currentaddress",
    "primaryaddress",
    "location",
    "地址",
    "当前地址",
    "现地址",
}
PAST_ADDRESS_KEYS = {
    "pastaddress",
    "pastaddresses",
    "previousaddress",
    "previousaddresses",
    "formeraddress",
    "曾用地址",
    "曾经地址",
    "历史地址",
}
FORMER_NAME_KEYS = {
    "formername",
    "formernames",
    "alias",
    "aliases",
    "othernames",
    "曾用名",
    "别名",
}
RELATIVE_KEYS = {"possiblerelative", "possiblerelatives", "relatives", "亲属", "可能亲属"}
ASSOCIATE_KEYS = {"possibleassociate", "possibleassociates", "associates", "关联人", "可能关联人"}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.()-]*)?(\d{3})[\s.()-]*(\d{3})[\s.-]*(\d{4})(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
STREET_RE = re.compile(
    r"\b(\d{1,6})\s+([A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,5}?)\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|"
    r"Circle|Cir|Way|Parkway|Pkwy|Place|Pl|Terrace|Ter|Trail|Trl)\b",
    re.I,
)


@dataclass(slots=True)
class CandidateScore:
    detail: ProfileResult
    score: int
    strong_categories: int
    exact_age: bool
    evidence: list[str]
    conflicts: list[str]


def _key(value: object) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "").strip().lower())


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _words(value: object) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


def _values(person: PersonInput, keys: set[str]) -> list[str]:
    found: list[str] = []
    for header, value in person.original.items():
        clean = _clean(value)
        if clean and _key(header) in keys:
            found.append(clean)
    return found


def _split_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(part.strip() for part in re.split(r"[|\r\n;]+", value) if part.strip())
    return result


def _phones(texts: list[str]) -> set[str]:
    found: set[str] = set()
    for text in texts:
        for match in PHONE_RE.finditer(text):
            found.add("".join(match.groups()))
    return found


def _emails(texts: list[str]) -> set[str]:
    return {match.group(0).lower() for text in texts for match in EMAIL_RE.finditer(text)}


def _zips(texts: list[str]) -> set[str]:
    return {match.group(1) for text in texts for match in ZIP_RE.finditer(text)}


def _streets(texts: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for text in texts:
        for match in STREET_RE.finditer(text):
            name_words = [
                word
                for word in _words(match.group(2))
                if word not in {"north", "south", "east", "west", "n", "s", "e", "w"}
            ]
            if name_words:
                found.append((match.group(1), name_words[0]))
    return found


def _age(value: object) -> int | None:
    match = re.search(r"\b(\d{1,3})\b", str(value or ""))
    if not match:
        return None
    number = int(match.group(1))
    return number if 0 < number < 125 else None


def _birthday_age(value: str) -> int | None:
    raw = _clean(value)
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            born = datetime.strptime(raw, fmt).date()
            today = date.today()
            return today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except ValueError:
            continue
    return None


def _name_evidence(person: PersonInput, detail: ProfileResult) -> tuple[int, list[str], list[str], bool]:
    input_names = [person.full_name, *_split_values(_values(person, FORMER_NAME_KEYS))]
    candidate_names = [detail.full_name, *_split_values([detail.former_names])]
    input_norm = {_compact(name) for name in input_names if _compact(name)}
    candidate_norm = {_compact(name) for name in candidate_names if _compact(name)}
    if input_norm & candidate_norm:
        return 32, ["姓名完全一致"], [], True

    expected_first = _compact(person.first_name)
    expected_last = _compact(person.last_name)
    candidate_words = _words(detail.full_name)
    if expected_first and expected_last and expected_first in candidate_words and expected_last in candidate_words:
        return 24, ["名和姓一致"], [], True

    best_ratio = 0.0
    for expected in input_norm:
        for actual in candidate_norm:
            best_ratio = max(best_ratio, SequenceMatcher(None, expected, actual).ratio())
    if best_ratio >= 0.92:
        return 20, ["姓名高度相似"], [], True
    if best_ratio >= 0.82:
        return 12, ["姓名相似"], [], True
    if best_ratio >= 0.70:
        return 6, ["姓名部分相似"], [], False
    return -24, [], ["姓名明显不一致"], False


def _location_text(detail: ProfileResult) -> str:
    return " | ".join(
        value
        for value in (
            detail.location,
            detail.result_summary,
            detail.profile_summary,
            detail.former_names,
        )
        if value
    )


def score_candidate(person: PersonInput, detail: ProfileResult) -> CandidateScore:
    score, evidence, conflicts, name_strong = _name_evidence(person, detail)
    strong: set[str] = {"name"} if name_strong else set()
    candidate_text = _location_text(detail)
    candidate_compact = _compact(candidate_text)

    input_phones = _phones(_values(person, PHONE_KEYS))
    candidate_phones = _phones([candidate_text])
    exact_phones = input_phones & candidate_phones
    last7 = {phone[-7:] for phone in input_phones} & {phone[-7:] for phone in candidate_phones}
    area_codes = {phone[:3] for phone in input_phones} & {phone[:3] for phone in candidate_phones}
    if exact_phones:
        score += 55
        evidence.append("完整手机号一致")
        strong.add("phone")
    elif last7:
        score += 35
        evidence.append("手机号后7位一致")
        strong.add("phone")
    elif area_codes:
        score += 4
        evidence.append("手机号前三位区号一致（弱证据）")
    elif input_phones and candidate_phones:
        score -= 8
        conflicts.append("公开手机号不一致")

    input_emails = _emails(_values(person, EMAIL_KEYS))
    candidate_emails = _emails([candidate_text])
    if input_emails & candidate_emails:
        score += 50
        evidence.append("完整邮箱一致")
        strong.add("email")
    elif input_emails and candidate_emails:
        score -= 6
        conflicts.append("公开邮箱不一致")

    current_addresses = _split_values(_values(person, CURRENT_ADDRESS_KEYS))
    past_addresses = _split_values(_values(person, PAST_ADDRESS_KEYS))
    exact_current = [
        address
        for address in current_addresses
        if _streets([address]) and len(_compact(address)) >= 9 and _compact(address) in candidate_compact
    ]
    exact_past = [
        address
        for address in past_addresses
        if _streets([address]) and len(_compact(address)) >= 9 and _compact(address) in candidate_compact
    ]
    if exact_current:
        score += 45
        evidence.append("完整当前地址一致")
        strong.add("address")
    elif exact_past:
        score += 32
        evidence.append("完整曾用地址一致")
        strong.add("address")

    current_zips = _zips(current_addresses)
    if person.zip_code and re.fullmatch(r"\d{5}(?:-\d{4})?", person.zip_code.strip()):
        current_zips.add(person.zip_code[:5])
    past_zips = _zips(past_addresses)
    candidate_zips = _zips([candidate_text])
    if current_zips & candidate_zips:
        score += 20
        evidence.append("当前邮编一致")
        strong.add("zip")
    elif past_zips & candidate_zips:
        score += 12
        evidence.append("曾用地址邮编一致")
        strong.add("zip")

    candidate_streets = _streets([candidate_text])
    current_streets = _streets(current_addresses)
    past_streets = _streets(past_addresses)

    def street_match(inputs: list[tuple[str, str]], prefix: bool = False) -> bool:
        for input_number, input_name in inputs:
            for candidate_number, candidate_name in candidate_streets:
                number_match = (
                    input_number[:2] == candidate_number[:2]
                    if prefix and len(input_number) >= 2 and len(candidate_number) >= 2
                    else input_number == candidate_number
                )
                if number_match and input_name == candidate_name:
                    return True
        return False

    if street_match(current_streets):
        score += 18
        evidence.append("当前门牌号和街道一致")
        strong.add("street")
    elif street_match(past_streets):
        score += 12
        evidence.append("曾用门牌号和街道一致")
        strong.add("street")
    elif street_match(current_streets, prefix=True):
        score += 7
        evidence.append("当前地址门牌号前2位和街道一致")
    elif street_match(past_streets, prefix=True):
        score += 5
        evidence.append("曾用地址门牌号前2位和街道一致")

    city = _compact(person.city)
    state = person.state.strip().lower()
    city_match = bool(city and city in candidate_compact)
    state_match = bool(state and re.search(rf"\b{re.escape(state)}\b", candidate_text, re.I))
    if city_match and state_match:
        score += 16
        evidence.append("城市和州一致")
        strong.add("city_state")
    elif city_match:
        score += 8
        evidence.append("城市一致")
    elif state_match:
        score += 3
        evidence.append("州一致（弱证据）")

    relative_names = {
        _compact(name)
        for name in _split_values(_values(person, RELATIVE_KEYS))
        if len(_compact(name)) >= 6
    }
    relative_hits = sum(1 for name in relative_names if name in candidate_compact)
    if relative_hits:
        score += min(16, relative_hits * 8)
        evidence.append(f"共同亲属一致{relative_hits}人")
        if relative_hits >= 2:
            strong.add("relatives")

    associate_names = {
        _compact(name)
        for name in _split_values(_values(person, ASSOCIATE_KEYS))
        if len(_compact(name)) >= 6
    }
    associate_hits = sum(1 for name in associate_names if name in candidate_compact)
    if associate_hits:
        score += min(6, associate_hits * 3)
        evidence.append(f"关联人一致{associate_hits}人（辅助证据）")

    input_age = _age(person.age)
    candidate_age = _age(detail.age)
    birthday_age = _birthday_age(detail.birthday)
    exact_age = bool(
        input_age is not None
        and (candidate_age == input_age or (candidate_age is None and birthday_age == input_age))
    )
    if exact_age:
        score += 30
        evidence.append("年龄完全一致")
        strong.add("age")
    elif input_age is not None and candidate_age is not None:
        difference = abs(input_age - candidate_age)
        if difference == 1:
            score += 8
            evidence.append("年龄相差1岁")
        elif difference <= 3:
            score += 2
            evidence.append("年龄接近")
        else:
            score -= 8
            conflicts.append("年龄不一致")

    if candidate_age is not None and birthday_age is not None:
        if abs(candidate_age - birthday_age) <= 1:
            score += 3
            evidence.append("生日与页面年龄自洽")
        else:
            score -= 8
            conflicts.append("生日与页面年龄冲突")

    return CandidateScore(
        detail=detail,
        score=score,
        strong_categories=len(strong),
        exact_age=exact_age,
        evidence=evidence,
        conflicts=conflicts,
    )


def _confidence(best: CandidateScore, runner_up: CandidateScore | None) -> str:
    margin = best.score - runner_up.score if runner_up else best.score
    strong = best.strong_categories
    if best.score >= 85 and strong >= 3 and margin >= 8:
        return "高"
    if best.score >= 55 and strong >= 2 and margin >= 4:
        return "中"
    return "低"


def select_best_identity(
    person: PersonInput,
    details: list[ProfileResult],
    total_candidates: int,
) -> ProfileResult:
    """按年龄分层后，用多源身份信号选择唯一候选；生日只用于同分决胜。"""

    if not details:
        return ProfileResult(
            result_index=0,
            profile_url="",
            full_name=person.full_name,
            location=person.location,
            query_strategy="年龄分层→多信号身份匹配",
            status="无结果",
            message="没有可用于匹配的候选",
        )

    scored = [score_candidate(person, detail) for detail in details]
    exact_age = [item for item in scored if item.exact_age]
    pool = exact_age or scored
    ranked = sorted(
        pool,
        key=lambda item: (item.score, item.strong_categories, bool(item.detail.birthday), -item.detail.result_index),
        reverse=True,
    )
    best = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    confidence = _confidence(best, runner_up)
    margin = best.score - runner_up.score if runner_up else best.score
    selected = best.detail
    age_reason = (
        f"先限定 {len(exact_age)} 个同龄候选"
        if exact_age
        else (f"没有年龄 {person.age} 的候选" if person.age else "输入未提供年龄")
    )
    evidence_text = "、".join(best.evidence[:8]) or "仅有弱匹配证据"
    conflict_text = f"；冲突={'、'.join(best.conflicts[:4])}" if best.conflicts else ""
    selected.status = f"已匹配生日（{confidence}置信度）" if selected.birthday else f"已匹配身份但生日未公开（{confidence}置信度）"
    selected.message = (
        f"{age_reason}；多信号得分={best.score}，领先={margin}，证据={evidence_text}{conflict_text}；"
        f"总候选={total_candidates}"
    )
    selected.query_strategy = f"{selected.query_strategy}→年龄分层→姓名/手机号/当前与曾用地址综合匹配"
    return selected
