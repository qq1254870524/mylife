from __future__ import annotations

import re
from datetime import datetime

from models import PersonInput, ProfileResult


ZODIAC_RANGES = (
    ((1, 20), "Aquarius", "January 20 - February 18"),
    ((2, 19), "Pisces", "February 19 - March 20"),
    ((3, 21), "Aries", "March 21 - April 19"),
    ((4, 20), "Taurus", "April 20 - May 20"),
    ((5, 21), "Gemini", "May 21 - June 20"),
    ((6, 21), "Cancer", "June 21 - July 22"),
    ((7, 23), "Leo", "July 23 - August 22"),
    ((8, 23), "Virgo", "August 23 - September 22"),
    ((9, 23), "Libra", "September 23 - October 22"),
    ((10, 23), "Scorpio", "October 23 - November 21"),
    ((11, 22), "Sagittarius", "November 22 - December 21"),
    ((12, 22), "Capricorn", "December 22 - January 19"),
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.()-]*)?(\d{3})[\s.()-]*(\d{3})[\s.-]*(\d{4})(?!\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
STREET_RE = re.compile(
    r"\b(\d{1,6})\s+([A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,5}?)\s+"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd|Court|Ct|"
    r"Circle|Cir|Way|Parkway|Pkwy|Place|Pl|Terrace|Ter|Trail|Trl)\b",
    re.I,
)


def _birthday_date(value: str) -> datetime | None:
    raw = re.sub(r"\s+", " ", str(value or "")).strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def zodiac_from_birthday(value: str) -> str:
    born = _birthday_date(value)
    if not born:
        return ""
    month_day = (born.month, born.day)
    if month_day >= (12, 22) or month_day <= (1, 19):
        return "Capricorn (December 22 - January 19)"
    for start, sign, date_range in ZODIAC_RANGES[:-1]:
        index = ZODIAC_RANGES.index((start, sign, date_range))
        next_start = ZODIAC_RANGES[index + 1][0]
        if start <= month_day < next_start:
            return f"{sign} ({date_range})"
    return ""


def _compact_name(value: str) -> tuple[str, str]:
    words = re.findall(r"[a-z]+", str(value or "").lower())
    return (words[0], words[-1]) if len(words) >= 2 else ("", "")


def _profile_text(detail: ProfileResult) -> str:
    return " | ".join((detail.location, detail.result_summary, detail.profile_summary, detail.former_names))


def _unique_signals(text: str) -> tuple[set[str], set[str], set[str]]:
    phones = {"".join(match.groups()) for match in PHONE_RE.finditer(text)}
    emails = {match.group(0).lower() for match in EMAIL_RE.finditer(text)}
    streets = {
        re.sub(r"[^a-z0-9]", "", match.group(0).lower())
        for match in STREET_RE.finditer(text)
    }
    return phones, emails, streets


def _same_identity_duplicate(selected: ProfileResult, donor: ProfileResult) -> bool:
    if _compact_name(selected.full_name) != _compact_name(donor.full_name):
        return False
    selected_age = int(selected.age) if str(selected.age).isdigit() else None
    donor_age = int(donor.age) if str(donor.age).isdigit() else None
    if selected_age is not None and donor_age is not None and abs(selected_age - donor_age) > 1:
        return False
    left = _unique_signals(_profile_text(selected))
    right = _unique_signals(_profile_text(donor))
    return any(a & b for a, b in zip(left, right))


def enrich_demographics(
    person: PersonInput,
    selected: ProfileResult,
    details: list[ProfileResult],
) -> ProfileResult:
    """只从严格同身份重复档案补字段；星座可由完整生日确定性计算。"""

    del person  # 预留给后续来源一致性规则；当前不根据姓名猜性别或生日。
    filled: list[str] = []
    for donor in details:
        if donor is selected or not _same_identity_duplicate(selected, donor):
            continue
        if not selected.birthday and donor.birthday:
            selected.birthday = donor.birthday
            filled.append("生日(同身份重复档案)")
        if not selected.gender and donor.gender:
            selected.gender = donor.gender
            filled.append("性别(同身份重复档案)")
        if not selected.zodiac and donor.zodiac:
            selected.zodiac = donor.zodiac
            filled.append("星座(同身份重复档案)")
    if selected.birthday and not selected.zodiac:
        selected.zodiac = zodiac_from_birthday(selected.birthday)
        if selected.zodiac:
            filled.append("星座(由完整生日确定)")
    if filled:
        extra = "、".join(dict.fromkeys(filled))
        selected.demographics_note = "；".join(x for x in (selected.demographics_note, extra) if x)
    return selected
