"""Collapse warehouse name variants onto one display name per player_id."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine import Query, count_star, fetch_dicts, game_line, tournament_line, trim
from bowlyzerapi.queries.filters import is_bye, is_player_game
from bowlyzerapi.queries.util import as_str
from bowlyzerapi.warehouse import data_revision

_CACHE: dict[str, str] | None = None
_CACHE_ALIASES: dict[str, tuple[str, ...]] | None = None
_CACHE_LABEL_TO_ID: dict[str, str] | None = None
_CACHE_REV: str | None = None


def pick_canonical_name(names: list[tuple[str, int]]) -> str:
    """Most common label; prefer ``Family, Given`` when counts tie."""

    def score(item: tuple[str, int]) -> tuple:
        name, n = item
        comma = 1 if "," in name else 0
        return (n, comma, len(name), name.lower())

    return max(names, key=score)[0]


def collapse_player_catalog(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, int]] = {}
    no_id: dict[str, int] = {}
    for row in rows:
        player_id = as_str(row.get("player_id") or row.get("id")) or ""
        name = as_str(row.get("name") or row.get("player_name")) or ""
        n = int(row.get("n") or 1)
        if not name:
            continue
        if player_id:
            counts = by_id.setdefault(player_id, {})
            counts[name] = counts.get(name, 0) + n
        else:
            no_id[name] = no_id.get(name, 0) + n
    players: list[dict[str, Any]] = []
    for player_id, counts in by_id.items():
        canonical = pick_canonical_name(list(counts.items()))
        aliases = sorted(label for label in counts if label != canonical)
        players.append({"id": player_id, "name": canonical, "aliases": aliases})
    for name in no_id:
        players.append({"id": "", "name": name, "aliases": []})
    players.sort(key=lambda row: (str(row["name"]).lower(), str(row["id"])))
    return players


def _ensure_identity_cache(con) -> None:
    global _CACHE, _CACHE_ALIASES, _CACHE_LABEL_TO_ID, _CACHE_REV
    revision = str(data_revision() or "")
    if _CACHE is not None and _CACHE_REV == revision:
        return
    g = game_line
    t = tournament_line
    rows = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_id, g.player_name.as_("name"), count_star().as_("n"))
        .where(
            is_player_game(g),
            ~is_bye(g.player_name),
            g.player_id.is_not_null(),
            trim(g.player_id) != "",
        )
        .group_by(g.player_id, g.player_name),
    )
    rows += fetch_dicts(
        con,
        Query()
        .from_(t)
        .select(t.player_id, t.player_name.as_("name"), count_star().as_("n"))
        .where(~is_bye(t.player_name), t.player_id.is_not_null(), trim(t.player_id) != "")
        .group_by(t.player_id, t.player_name),
    )
    names: dict[str, str] = {}
    aliases: dict[str, tuple[str, ...]] = {}
    label_ids: dict[str, set[str]] = {}
    for row in collapse_player_catalog(rows):
        player_id = row["id"]
        if not player_id:
            continue
        labels = (row["name"], *row["aliases"])
        names[player_id] = row["name"]
        aliases[player_id] = labels
        for label in labels:
            key = str(label).casefold()
            if not key:
                continue
            label_ids.setdefault(key, set()).add(player_id)
    _CACHE = names
    _CACHE_ALIASES = aliases
    _CACHE_LABEL_TO_ID = {key: next(iter(ids)) for key, ids in label_ids.items() if len(ids) == 1}
    _CACHE_REV = revision


def canonical_player_names(con) -> dict[str, str]:
    _ensure_identity_cache(con)
    return _CACHE or {}


def identity_labels(con, player_id: str | None, player_name: str | None) -> list[str]:
    """Canonical name plus aliases for this id, plus the resolved display name."""
    _ensure_identity_cache(con)
    labels: list[str] = []
    seen: set[str] = set()

    def add(name: str | None) -> None:
        text = (name or "").strip()
        key = text.casefold()
        if not text or key in seen:
            return
        seen.add(key)
        labels.append(text)

    add(player_name)
    pid = (player_id or "").strip()
    if pid:
        add((_CACHE or {}).get(pid))
        for alias in (_CACHE_ALIASES or {}).get(pid, ()):
            add(alias)
    return labels


def fold_orphan_player_ids(
    con,
    rows: list[dict[str, Any]],
    *,
    name_key: str = "player_name",
    id_key: str = "player_id",
) -> None:
    """Attach a catalog id to name-only rows when that label uniquely maps to one player."""
    _ensure_identity_cache(con)
    mapping = _CACHE_LABEL_TO_ID or {}
    for row in rows:
        if as_str(row.get(id_key) or row.get("id")):
            continue
        name = as_str(row.get(name_key) or row.get("name")) or ""
        mapped = mapping.get(name.casefold())
        if mapped:
            row[id_key] = mapped


def apply_canonical_player_names(con, rows: list[dict[str, Any]], *, name_key: str = "player_name") -> None:
    names = canonical_player_names(con)
    for row in rows:
        player_id = as_str(row.get("player_id") or row.get("id")) or ""
        if player_id and player_id in names:
            row[name_key] = names[player_id]
