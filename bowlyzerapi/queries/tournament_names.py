"""Stable tournament group labels (mirrors Flask ``normalize_tournament_group_name``)."""

from __future__ import annotations

import re

_YEAR_SUFFIX_RE = re.compile(r"\s+20\d{2}\s*$")

_GROUP_CANONICAL_ALIASES: dict[str, str] = {
    "Bayerische Meisterschaft - Männer Einzel": "Bayerische Meisterschaft Einzel",
    "Bayerische Meisterschaft Männer Einzel": "Bayerische Meisterschaft Einzel",
    "Bayerische Meisterschaft - Frauen Einzel": "Bayerische Meisterschaft Einzel Damen",
    "Bayerische Meisterschaft - Damen Einzel": "Bayerische Meisterschaft Einzel Damen",
    "Bayerische Meisterschaft Damen Einzel": "Bayerische Meisterschaft Einzel Damen",
    "Südbayerische Meisterschaft": "Südbayerische Meisterschaft Einzel",
    "Südbayerische Meisterschaft Männer Einzel": "Südbayerische Meisterschaft Einzel",
    "Südbayerische Meisterschaft - Männer Einzel": "Südbayerische Meisterschaft Einzel",
    "Südbayerische Meisterschaft Damen Einzel": "Südbayerische Meisterschaft Einzel Damen",
    "Südbayerische Meisterschaft - Damen Einzel": "Südbayerische Meisterschaft Einzel Damen",
    "Südbayerische Meisterschaft - Frauen Einzel": "Südbayerische Meisterschaft Einzel Damen",
    "Nordbayerische Meisterschaft": "Nordbayrische Meisterschaft Einzel",
    "Nordbayrische Meisterschaft": "Nordbayrische Meisterschaft Einzel",
    "Nordbayerische Meisterschaft Männer Einzel": "Nordbayrische Meisterschaft Einzel",
    "Nordbayerische Meisterschaft - Männer Einzel": "Nordbayrische Meisterschaft Einzel",
    "Nordbayerische Meisterschaft Damen Einzel": "Nordbayrische Meisterschaft Einzel Damen",
    "Nordbayerische Meisterschaft - Damen Einzel": "Nordbayrische Meisterschaft Einzel Damen",
    "Nordbayerische Meisterschaft - Frauen Einzel": "Nordbayrische Meisterschaft Einzel Damen",
    "Nordbayerische Meisterschaft Einzel Herren": "Nordbayrische Meisterschaft Einzel",
    "Bayerische Meisterschaft Einzel Herren": "Bayerische Meisterschaft Einzel",
    "Bayerische Meisterschaft Doppel": "Bayerische Meisterschaft Männer Doppel",
}

_MAPPING_ROWS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Nordbayerische Meisterschaft",
        (
            "Nordbayerische Meisterschaft Männer Einzel",
            "Nordbayerische Meisterschaft - Männer Einzel",
        ),
    ),
    (
        "Nordbayerische Meisterschaft Damen Einzel",
        (
            "Nordbayerische Meisterschaft - Damen Einzel",
            "Nordbayerische Meisterschaft - Frauen Einzel",
        ),
    ),
    ("Nordbayerische Meisterschaft Männer Doppel", ("Nordbayerische Meisterschaft - Männer Doppel",)),
    (
        "Südbayerische Meisterschaft",
        (
            "Südbayerische Meisterschaft Männer Einzel",
            "Südbayerische Meisterschaft - Männer Einzel",
        ),
    ),
    (
        "Südbayerische Meisterschaft Damen Einzel",
        (
            "Südbayerische Meisterschaft - Damen Einzel",
            "Südbayerische Meisterschaft - Frauen Einzel",
        ),
    ),
    ("Südbayerische Meisterschaft Männer Doppel", ("Südbayerische Meisterschaft - Männer Doppel",)),
    ("Bayerische Meisterschaft - Männer Einzel", ("Bayerische Meisterschaft Männer Einzel",)),
    (
        "Bayerische Meisterschaft - Frauen Einzel",
        (
            "Bayerische Meisterschaft Damen Einzel",
            "Bayerische Meisterschaft - Damen Einzel",
        ),
    ),
    ("Bayerische Meisterschaft Männer Doppel", ("Bayerische Meisterschaft - Männer Doppel",)),
    (
        "Bayerische Meisterschaft Damen Doppel",
        (
            "Bayerische Meisterschaft - Damen Doppel",
            "Bayerische Meisterschaft - Frauen Doppel",
        ),
    ),
]


def _group_alias_lookup() -> dict[str, str]:
    lookup = dict(_GROUP_CANONICAL_ALIASES)
    for long_name, aliases in _MAPPING_ROWS:
        canonical = _GROUP_CANONICAL_ALIASES.get(long_name, long_name)
        lookup[long_name] = canonical
        for alias in aliases:
            lookup[alias] = canonical
    return lookup


_LOOKUP = _group_alias_lookup()


def normalize_tournament_group_name(event_name: str | None) -> str:
    text = str(event_name or "").strip()
    if not text:
        return ""
    stripped = _YEAR_SUFFIX_RE.sub("", text).strip()
    return _LOOKUP.get(stripped, stripped)
