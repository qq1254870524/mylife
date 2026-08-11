from __future__ import annotations

from collections.abc import Iterable, Mapping


FullRowKey = tuple[tuple[str, str], ...]


def full_row_key(
    original: Mapping[str, object],
    headers: Iterable[str] | None = None,
) -> FullRowKey:
    """按全部列名和规范化单元格值生成稳定键，首列从不单独决定重复。"""

    ordered_headers = list(headers) if headers is not None else sorted(str(key) for key in original)
    return tuple(
        (str(header), str(original.get(str(header), "") or "").strip())
        for header in ordered_headers
    )
