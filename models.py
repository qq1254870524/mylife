from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PersonInput:
    source_path: Path
    source_row: int
    headers: list[str]
    original: dict[str, str]
    first_value: str
    first_name: str = ""
    middle_name: str = ""
    last_name: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    validation_error: str = ""

    @property
    def full_name(self) -> str:
        return " ".join(x for x in (self.first_name, self.middle_name, self.last_name) if x).strip()

    @property
    def location(self) -> str:
        city_state = ", ".join(x for x in (self.city, self.state) if x)
        return " ".join(x for x in (city_state, self.zip_code) if x).strip()


@dataclass(slots=True)
class SearchResult:
    profile_url: str
    full_name: str = ""
    age: str = ""
    location: str = ""
    former_names: str = ""
    result_summary: str = ""


@dataclass(slots=True)
class ProfileResult:
    result_index: int
    profile_url: str
    full_name: str = ""
    age: str = ""
    birthday: str = ""
    location: str = ""
    former_names: str = ""
    result_summary: str = ""
    profile_summary: str = ""
    query_strategy: str = ""
    status: str = "已提取详情"
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_index": self.result_index,
            "full_name": self.full_name,
            "age": self.age,
            "birthday": self.birthday,
            "location": self.location,
            "former_names": self.former_names,
            "profile_url": self.profile_url,
            "result_summary": self.result_summary,
            "profile_summary": self.profile_summary,
            "query_strategy": self.query_strategy,
            "status": self.status,
            "message": self.message,
        }


@dataclass(slots=True)
class RunConfig:
    input_file: Path
    output_dir: Path
    thread_count: int = 1
    browser_mode: str = "小窗口"
    proxy_enabled: bool = False
    proxy_lines: list[str] = field(default_factory=list)
    max_job_attempts: int = 3
    max_search_pages: int = 50
