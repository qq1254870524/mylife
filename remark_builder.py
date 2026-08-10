from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_result_remark(result: Mapping[str, Any]) -> str:
    """把内部匹配过程压缩为适合 CSV 最后一列的可审计原因。"""

    strategy = str(result.get("query_strategy") or "").strip()
    coverage = str(result.get("search_coverage") or "").strip()
    status = str(result.get("status") or "").strip()
    message = str(result.get("message") or "").strip()

    search_method = strategy.split("→年龄分层", 1)[0].strip("→ ")
    if search_method.startswith("姓名+城市州邮编"):
        method = "姓名+城市州邮编"
    elif search_method.startswith("姓名"):
        method = "姓名"
    elif search_method:
        method = search_method
    else:
        method = "未执行搜索"

    parts = [f"搜索方式：{method}"]
    if coverage and (coverage != search_method or "→" in coverage):
        parts.append(f"搜索范围：{coverage}")
    if status:
        parts.append(f"结果：{status}")
    if message:
        parts.append(f"原因：{message}")
    return "；".join(parts)
