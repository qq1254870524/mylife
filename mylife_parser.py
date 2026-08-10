from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote_plus, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from demographics_enricher import zodiac_from_birthday
from models import ProfileResult, SearchResult


PROFILE_PATH_RE = re.compile(r"^/[a-z0-9_-]+/[ce]\d+/?$", re.I)
AGE_RE = re.compile(r"\b(?:Age\s*)?(\d{1,3})\s*(?:years?\s*old|yrs?\.?\s*old)?\b", re.I)
MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
ZODIAC = r"(?:Aries|Taurus|Gemini|Cancer|Leo|Virgo|Libra|Scorpio|Sagittarius|Capricorn|Aquarius|Pisces)"
BIRTHDAY_PATTERNS = (
    re.compile(rf"\b(?:Birthday|Date\s+of\s+Birth|Born(?:\s+on)?)\s*[:\-]?\s*({MONTH}\s+\d{{1,2}},\s*\d{{4}})", re.I),
    re.compile(r"\b(?:Birthday|Date\s+of\s+Birth|Born(?:\s+on)?)\s*[:\-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})", re.I),
)


def build_search_url(first_name: str, last_name: str, location: str = "") -> str:
    params = [
        f"searchFirstName={quote_plus(first_name.strip())}",
        f"searchLastName={quote_plus(last_name.strip())}",
        f"searchLocation={quote_plus(location.strip())}",
        "whyReg=peoplesearch",
        "whySub=Member+Profile+Sub",
        "pageType=ps",
    ]
    if not location:
        params.insert(0, f"search={quote_plus((first_name + ' ' + last_name).strip())}")
    return "https://www.mylife.com/pub-multisearch.pubview?" + "&".join(params)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _absolute_profile_url(href: str, base_url: str) -> str:
    absolute = urljoin(base_url, href)
    parts = urlsplit(absolute)
    if parts.netloc.lower() not in {"mylife.com", "www.mylife.com"}:
        return ""
    if not PROFILE_PATH_RE.match(parts.path):
        return ""
    return urlunsplit(("https", "www.mylife.com", parts.path.rstrip("/"), "", ""))


def _card_for(anchor: Tag) -> Tag:
    for parent in anchor.parents:
        if not isinstance(parent, Tag):
            continue
        classes = " ".join(parent.get("class", []))
        if parent.name in {"article", "li"} or re.search(r"result|profile|person|search", classes, re.I):
            text = _clean_text(parent.get_text(" ", strip=True))
            if len(text) <= 1500:
                return parent
    return anchor


def _name_from(anchor: Tag, card_text: str) -> str:
    text = _clean_text(anchor.get_text(" ", strip=True))
    text = re.sub(r",\s*\d{1,3}\b.*$", "", text)
    text = re.sub(r"\s+\d(?:\.\d+)?/5\s*$", "", text)
    text = re.sub(r"\s+View\s+Profile.*$", "", text, flags=re.I)
    if 2 <= len(text.split()) <= 8 and not re.search(r"search|view|report|reputation", text, re.I):
        return text
    match = re.search(r"(?:Name\s*[:\-]\s*)?([A-Z][A-Za-z'\-.]+(?:\s+[A-Z][A-Za-z'\-.]+){1,4})", card_text)
    return match.group(1) if match else ""


def extract_age(text: str) -> str:
    clean = _clean_text(text)
    for pattern in (
        re.compile(r"\bAge\s*[:\-]?\s*(\d{1,3})\b", re.I),
        re.compile(r",\s*(\d{1,3})\b"),
        re.compile(r"\b(\d{1,3})\s*(?:years?\s*old|yrs?\.?\s*old)\b", re.I),
    ):
        match = pattern.search(clean)
        if match and 0 < int(match.group(1)) < 125:
            return str(int(match.group(1)))
    return ""


def _field(text: str, labels: str) -> str:
    match = re.search(rf"(?:{labels})\s*[:\-]?\s*(.+?)(?=(?:Age|Lives?|Location|Aliases?|Former|View|$)\s*[:\-]?)", text, re.I)
    return _clean_text(match.group(1)) if match else ""


def parse_search_results(html: str, base_url: str) -> tuple[list[SearchResult], str, bool]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[SearchResult] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        profile_url = _absolute_profile_url(str(anchor.get("href", "")), base_url)
        if not profile_url or profile_url in seen:
            continue
        seen.add(profile_url)
        card = _card_for(anchor)
        card_text = _clean_text(card.get_text(" ", strip=True))
        age = extract_age(card_text)
        results.append(
            SearchResult(
                profile_url=profile_url,
                full_name=_name_from(anchor, card_text),
                age=age,
                location=_field(card_text, r"Lives?\s+in|Location|Current\s+City"),
                former_names=_field(card_text, r"Aliases?|Former\s+Names?|Also\s+Known\s+As"),
                result_summary=card_text[:1000],
            )
        )
    next_url = ""
    for anchor in soup.find_all("a", href=True):
        rel = " ".join(anchor.get("rel", [])) if isinstance(anchor.get("rel"), list) else str(anchor.get("rel", ""))
        text = _clean_text(anchor.get_text(" ", strip=True))
        aria = str(anchor.get("aria-label", ""))
        if "next" in rel.lower() or re.fullmatch(r"(?:next|next page|›|»|下一页)", text or aria, re.I):
            next_url = urljoin(base_url, str(anchor["href"]))
            break
    body = _clean_text(soup.get_text(" ", strip=True)).lower()
    no_results = any(marker in body for marker in ("no results", "no matches", "couldn't find", "did not find", "没有结果"))
    return results, next_url, no_results


def extract_birthday(text: str, html: str = "") -> str:
    clean = _clean_text(text)
    for pattern in BIRTHDAY_PATTERNS:
        match = pattern.search(clean)
        if match:
            return _clean_text(match.group(1))
    raw = unescape(html or "")
    match = re.search(
        r'''["'](?:birthDate|dateOfBirth|birthday|dob)["']\s*:\s*["'](\d{4})-(\d{2})-(\d{2})["']''',
        raw,
        re.I,
    )
    if match:
        return f"{match.group(2)}/{match.group(3)}/{match.group(1)}"
    match = re.search(
        r'''["'](?:birthDate|dateOfBirth|birthday|dob)["']\s*:\s*["'](\d{1,2}[/-]\d{1,2}[/-]\d{4})["']''',
        raw,
        re.I,
    )
    if match:
        return match.group(1)
    return ""


def extract_gender(html: str, text: str = "") -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    clean = _clean_text(text or soup.get_text(" ", strip=True))
    match = re.search(r"\b(?:Gender|Sex)\s*[:\-]?\s*(Female|Male|Non[- ]?binary)\b", clean, re.I)
    if match:
        return match.group(1).replace("-", " ").title()
    raw_match = re.search(
        r'''["'](?:gender|sex)["']\s*:\s*["'](Female|Male|Non[- ]?binary)["']''',
        unescape(html or ""),
        re.I,
    )
    if raw_match:
        return raw_match.group(1).replace("-", " ").title()
    for span in soup.find_all("span"):
        value = _clean_text(span.get_text(" ", strip=True))
        if re.fullmatch(r"Female|Male|Non[- ]?binary", value, re.I):
            return value.replace("-", " ").title()
    return ""


def extract_zodiac(html: str, text: str = "") -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup.find_all(["font", "span", "div"]):
        value = _clean_text(node.get_text(" ", strip=True))
        match = re.fullmatch(rf"({ZODIAC})(?:\s*(\([^)]{{3,80}}\)))?", value, re.I)
        if match:
            sign = match.group(1).title()
            return f"{sign} {match.group(2)}" if match.group(2) else sign
    clean = _clean_text(text or soup.get_text(" ", strip=True))
    match = re.search(rf"\b({ZODIAC})(?:\s*(\([^)]{{3,80}}\)))?", clean, re.I)
    if not match:
        return ""
    sign = match.group(1).title()
    return f"{sign} {match.group(2)}" if match.group(2) else sign


def parse_profile_html(html: str, seed: SearchResult, result_index: int, strategy: str) -> ProfileResult:
    soup = BeautifulSoup(html or "", "html.parser")
    text = _clean_text(soup.get_text(" ", strip=True))
    heading = soup.find(["h1", "h2"])
    heading_text = _clean_text(heading.get_text(" ", strip=True)) if heading else ""
    full_name = heading_text or seed.full_name
    full_name = re.sub(r",\s*\d{1,3}\b.*$", "", full_name)
    full_name = re.sub(r"\s+(?:Reputation|Profile|Background).*$", "", full_name, flags=re.I)
    age = extract_age(heading_text) or extract_age(text) or seed.age
    location = _field(text[:8000], r"Lives?\s+in|Current\s+Address|Location") or seed.location
    former_names = _field(text[:8000], r"Aliases?|Former\s+Names?|Also\s+Known\s+As") or seed.former_names
    birthday = extract_birthday(text, html)
    gender = extract_gender(html, text)
    zodiac = extract_zodiac(html, text)
    sources: list[str] = []
    if birthday:
        sources.append("生日=MyLife详情")
    if gender:
        sources.append("性别=MyLife详情")
    if not zodiac and birthday:
        zodiac = zodiac_from_birthday(birthday)
        if zodiac:
            sources.append("星座=完整生日确定")
    elif zodiac:
        sources.append("星座=MyLife详情")
    return ProfileResult(
        result_index=result_index,
        profile_url=seed.profile_url,
        full_name=full_name or seed.full_name,
        age=age,
        birthday=birthday,
        gender=gender,
        zodiac=zodiac,
        location=location,
        former_names=former_names,
        result_summary=seed.result_summary,
        # 身份匹配模块需要读取页面后段的电话和曾用地址；数据库保留到 20K 字符，CSV 不输出此字段。
        profile_summary=text[:20000],
        query_strategy=strategy,
        demographics_note="、".join(sources),
        message="" if birthday else "详情页未公开生日",
    )
