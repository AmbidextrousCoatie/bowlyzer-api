from __future__ import annotations

from typing import Any


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
