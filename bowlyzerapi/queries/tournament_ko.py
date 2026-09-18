"""KO brackets from tournament_line rows + publish-dir config JSON.

Mirrors Flask ``TournamentService._build_ko_bracket_payload`` without pandas.
Config files stay next to published Parquet (not DuckDB).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from bowlyzerapi.queries.tournament_names import normalize_tournament_group_name
from bowlyzerapi.queries.util import as_int, as_str
from bowlyzerapi.warehouse import parquet_dir

KO_TREE = "tree"
KO_STEPLADDER = "seeded_elim_stepladder"
SERIES_BO3 = "bo3_pins"
SERIES_SCRATCH_2G = "scratch_total_2g"
SERIES_SINGLE = "single_game"

_KO_ROUND_RE = re.compile(r"KO|Eliminierung|Step[\s\-]?ladder", re.IGNORECASE)


def is_ko_round_name(name: str) -> bool:
    return bool(_KO_ROUND_RE.search(str(name or "")))


def empty_bracket(*, series: str = SERIES_BO3, fmt: str = KO_TREE, basis: str = "scratch") -> dict[str, Any]:
    return {
        "matches": [],
        "placements": [],
        "finalist_a": None,
        "finalist_b": None,
        "path_keys_a": [],
        "path_keys_b": [],
        "palette_index_a": 2,
        "palette_index_b": 8,
        "ko_finale_series": series,
        "ko_bracket_format": fmt,
        "ko_decision_basis": basis,
    }


def build_ko_bracket(season: str, event: str, games: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = config_entry(season, event)
    fmt = _bracket_format(cfg)
    if fmt == KO_STEPLADDER:
        return _elim_stepladder(season, event, games, cfg)
    return _tree_bracket(season, event, games, cfg)


def ko_finale_round_number(games: list[dict[str, Any]]) -> int | None:
    nums = [as_int(row.get("round_number")) for row in games if is_ko_round_name(as_str(row.get("round_name")))]
    nums = [n for n in nums if n is not None]
    return max(nums) if nums else None


def format_ko_fields(season: str, event: str, games: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = config_entry(season, event)
    ko_rn = ko_finale_round_number(games)
    if not cfg and ko_rn is None:
        return {"ko_finale_round_number_in_data": None}
    fmt = _bracket_format(cfg)
    basis = _decision_basis(cfg)
    series = _finale_series(cfg)
    if fmt == KO_STEPLADDER:
        hdc = " inkl. Handicap" if basis == "handicap" else ""
        label_de = (
            f"Finale{hdc}: Eliminierung Spiel-für-Spiel (niedrigster raus, Plätze 4–6) → "
            "Stepladder 1 Spiel → Finale Best-of-3 (#1 vs Stepladder-Sieger)"
        )
        label_en = (
            f"Finals{(' incl. handicap' if basis == 'handicap' else '')}: "
            "per-game elimination (lowest out, places 4–6) → "
            "1-game stepladder → best-of-3 final (#1 vs stepladder winner)"
        )
    elif series == SERIES_SCRATCH_2G:
        label_de = "KO-Finale: zwei Spiele Scratch-Gesamt"
        label_en = "KO finals: two-game scratch series total"
    else:
        label_de = "KO-Finale: Best-of-3 (Pinfall je Spiel)"
        label_en = "KO finals: best-of-3 pinfall"
    span = None
    rank = as_int(cfg.get("ko_qualifying_cut_rank"))
    first = as_int(cfg.get("ko_qualifying_cut_round"))
    through = as_int(cfg.get("ko_qualifying_cut_through_round"))
    if rank and first and through:
        span = {"rank": rank, "first_round": first, "through_round": through}
    pair = {"round": through, "rank": rank} if rank and through else None
    public = {}
    for key in (
        "ko_finale_series",
        "ko_bracket_format",
        "ko_decision_basis",
        "ko_qualifying_cut_rank",
        "ko_qualifying_cut_round",
        "ko_qualifying_cut_through_round",
        "ko_finale_phases",
        "player_cards",
        "standings_note",
    ):
        if key in cfg:
            public[key] = cfg[key]
    return {
        "ko_finale_round_number_in_data": ko_finale_round_number(games),
        "ko_bracket_format": fmt,
        "ko_decision_basis": basis,
        "ko_finale_series": series,
        "ko_finale_series_label_de": label_de,
        "ko_finale_series_label_en": label_en,
        "qualifying_cut_span": span,
        "qualifying_cut_pair": pair,
        "config": public,
        "config_note": as_str(cfg.get("standings_note") or cfg.get("_comment")) or None,
    }


def apply_ko_ranks(leaderboard: list[dict[str, Any]], bracket: dict[str, Any]) -> list[dict[str, Any]]:
    placements = list(bracket.get("placements") or [])
    if not placements or not leaderboard:
        return leaderboard
    by_name: dict[str, int] = {}
    for item in placements:
        key = _norm(_strip_no_show(as_str(item.get("player"))))
        place = as_int(item.get("place"))
        if key and place:
            by_name[key] = place
    rows = []
    for row in leaderboard:
        item = dict(row)
        key = _norm(_strip_no_show(as_str(item.get("player") or item.get("player_name"))))
        place = by_name.get(key)
        if place is not None:
            item["rank"] = place
            item["ko_place"] = place
        rows.append(item)
    if as_str(bracket.get("ko_bracket_format")) == KO_STEPLADDER:
        finals = [row for row in rows if row.get("ko_place") is not None]
        rest = [row for row in rows if row.get("ko_place") is None]
        finals.sort(key=lambda row: int(row.get("ko_place") or 99))
        rest.sort(key=lambda row: (-float(row.get("avg_net") or row.get("avg_scratch") or 0), as_str(row.get("player"))))
        ranked = finals + rest
        next_place = max((int(row.get("ko_place") or 0) for row in finals), default=0) + 1
        for row in rest:
            row["rank"] = next_place
            next_place += 1
        return ranked
    return rows


def player_in_bracket(bracket: dict[str, Any] | None, player: str) -> bool:
    if not bracket or not bracket.get("matches"):
        return False
    target = _norm(_strip_no_show(player))
    if not target:
        return False
    for match in bracket["matches"]:
        if match.get("kind") == "field":
            for person in match.get("field") or []:
                if _norm(_strip_no_show(as_str(person.get("name")))) == target:
                    return True
            continue
        for side in ("side_a", "side_b"):
            if _norm(_strip_no_show(as_str((match.get(side) or {}).get("name")))) == target:
                return True
    return False


def placement_for(bracket: dict[str, Any] | None, player: str) -> int | None:
    if not bracket:
        return None
    target = _norm(_strip_no_show(player))
    for item in bracket.get("placements") or []:
        if _norm(_strip_no_show(as_str(item.get("player")))) == target:
            place = as_int(item.get("place"))
            return place if place and place > 0 else None
    return None


def with_highlights(bracket: dict[str, Any], player: str) -> dict[str, Any]:
    target = _norm(_strip_no_show(player))
    matches = []
    for match in bracket.get("matches") or []:
        item = dict(match)
        if match.get("kind") == "field":
            field = []
            for person in match.get("field") or []:
                row = dict(person)
                row["highlight"] = _norm(_strip_no_show(as_str(row.get("name")))) == target
                field.append(row)
            item["field"] = field
            sa, sb = dict(match.get("side_a") or {}), dict(match.get("side_b") or {})
            sa["highlight"] = _norm(_strip_no_show(as_str(sa.get("name")))) == target
            sb["highlight"] = False
            item["side_a"], item["side_b"] = sa, sb
        else:
            sa, sb = dict(match.get("side_a") or {}), dict(match.get("side_b") or {})
            sa["highlight"] = _norm(_strip_no_show(as_str(sa.get("name")))) == target
            sb["highlight"] = _norm(_strip_no_show(as_str(sb.get("name")))) == target
            item["side_a"], item["side_b"] = sa, sb
        matches.append(item)
    return {**bracket, "matches": matches, "focus_player": player.strip()}


def config_entry(season: str, event: str) -> dict[str, Any]:
    raw = _load_config()
    season_s = str(season).strip()
    event_s = str(event).strip()
    direct = raw.get(f"{season_s}||{event_s}")
    if isinstance(direct, dict):
        return direct
    group = normalize_tournament_group_name(event_s)
    for key, block in raw.items():
        if not isinstance(block, dict) or "||" not in str(key):
            continue
        s, name = str(key).split("||", 1)
        if s.strip() != season_s:
            continue
        if name.strip() == event_s or normalize_tournament_group_name(name) == group:
            return block
    return {}


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    path = parquet_dir() / "tournament_ko_config.json"
    return _read_json(path)


def _load_overrides() -> dict[str, Any]:
    return _read_json(parquet_dir() / "ko_bracket_overrides.json")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _bracket_format(cfg: dict[str, Any]) -> str:
    value = _fold(cfg.get("ko_bracket_format"))
    if value in {KO_STEPLADDER, "elim_stepladder", "stepladder"}:
        return KO_STEPLADDER
    return KO_TREE


def _decision_basis(cfg: dict[str, Any]) -> str:
    value = _fold(cfg.get("ko_decision_basis"))
    if value in {"handicap", "hdc", "net", "inkl_hdc", "inkl. hdc", "with_handicap"}:
        return "handicap"
    return "scratch"


def _finale_series(cfg: dict[str, Any]) -> str:
    if _bracket_format(cfg) == KO_STEPLADDER:
        phases = cfg.get("ko_finale_phases") if isinstance(cfg.get("ko_finale_phases"), dict) else {}
        final = phases.get("final") if isinstance(phases, dict) else None
        if isinstance(final, dict):
            return _series_mode(final.get("series_mode"), SERIES_BO3)
        return SERIES_BO3
    return _series_mode(cfg.get("ko_finale_series"), SERIES_BO3)


def _series_mode(raw: Any, default: str) -> str:
    value = _fold(raw)
    if value in {SERIES_SCRATCH_2G, "scratch_total", "scratch_2g", "2g_scratch"}:
        return SERIES_SCRATCH_2G
    if value in {SERIES_BO3, "bo3", "pins"}:
        return SERIES_BO3
    if value in {SERIES_SINGLE, "single"}:
        return SERIES_SINGLE
    return default


def _fold(value: Any) -> str:
    return (as_str(value) or "").casefold()


def _norm(name: str) -> str:
    return _fold(name)


def _strip_no_show(name: str) -> str:
    raw = str(name or "").strip()
    low = raw.lower()
    if low.endswith("(no show)"):
        return raw[: low.rfind("(")].strip()
    return raw


def _is_bye(name: str) -> bool:
    n = str(name or "").strip().lower()
    if not n:
        return True
    if "(no show)" in n or "nicht angetreten" in n:
        return True
    return n in {"bye", "freilos", "tbd"}


def _display_name(name: str) -> str:
    raw = str(name or "").strip()
    if not raw:
        return raw
    if "(no show)" in raw.lower():
        return raw
    if _norm(raw) == _norm("Nicht angetreten"):
        return "Nicht angetreten (No show)"
    return raw


def _match_keys(n: int) -> list[str]:
    if n <= 0:
        return []
    if n == 1:
        return ["F"]
    if n == 2:
        return ["SF1", "F"]
    if n == 3:
        return ["SF1", "SF2", "F"]
    if n == 4:
        return ["QF1", "QF2", "SF1", "F"]
    if n == 5:
        return ["QF1", "QF2", "SF1", "SF2", "F"]
    return [f"M{i + 1}" for i in range(n)]


def _tree_phase(index: int, n: int) -> str:
    if n <= 1:
        return "final"
    if index == n - 1:
        return "final"
    if n in {4, 5}:
        return "qf" if index < 2 else "sf"
    if n in {2, 3}:
        return "sf"
    return "early" if index < n - 2 else "late"


def _ko_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in games:
        if not is_ko_round_name(as_str(row.get("round_name"))):
            continue
        gn = as_int(row.get("game_number"))
        if gn is None:
            continue
        rows.append(row)
    return rows


def _players_for_game(rows: list[dict[str, Any]], *, use_hdc: bool) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = as_str(row.get("player_name"))
        if not name:
            continue
        scratch = as_int(row.get("score")) or 0
        hdc = as_int(row.get("handicap")) or 0
        pins = scratch + (hdc if use_hdc else 0)
        key = name.casefold()
        prev = by_name.get(key)
        if prev is None:
            by_name[key] = {
                "name": name,
                "id": as_str(row.get("player_id")),
                "score": pins,
                "scratch": scratch,
                "handicap": hdc,
                "club": as_str(row.get("club")),
                "stage_rank": as_int(row.get("stage_rank")),
            }
        else:
            prev["score"] += pins
            prev["scratch"] += scratch
            prev["handicap"] += hdc
            if row.get("player_id") and not prev.get("id"):
                prev["id"] = as_str(row.get("player_id"))
            if as_int(row.get("stage_rank")) is not None:
                prev["stage_rank"] = as_int(row.get("stage_rank"))
    return list(by_name.values())


def _games_by_number(games: list[dict[str, Any]]) -> list[tuple[int, list[dict[str, Any]]]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for row in games:
        gn = as_int(row.get("game_number"))
        if gn is None:
            continue
        if gn not in buckets:
            buckets[gn] = []
            order.append(gn)
        buckets[gn].append(row)
    return [(gn, buckets[gn]) for gn in sorted(order)]


def _pair_clusters(blocks: list[tuple[int, tuple[str, str], dict[str, Any], dict[str, Any]]]) -> list[list[tuple]]:
    clusters: list[list[tuple]] = []
    cur: list[tuple] = []
    cur_key: tuple[str, str] | None = None
    for gn, key, p0, p1 in blocks:
        if not cur:
            cur_key = key
            cur.append((gn, p0, p1))
            continue
        if key == cur_key:
            cur.append((gn, p0, p1))
        else:
            clusters.append(cur)
            cur_key = key
            cur = [(gn, p0, p1)]
    if cur:
        clusters.append(cur)
    return clusters


def _winner_name(match: dict[str, Any]) -> str:
    if match.get("winner") == "a":
        return as_str((match.get("side_a") or {}).get("name"))
    if match.get("winner") == "b":
        return as_str((match.get("side_b") or {}).get("name"))
    return ""


def _tiebreak(cluster: list[tuple], side_a: str, side_b: str) -> str | None:
    if not cluster:
        return None
    _gn, lp0, lp1 = cluster[-1]

    def sr_for(name: str) -> int | None:
        for px in (lp0, lp1):
            if _norm(px["name"]) == _norm(name):
                return as_int(px.get("stage_rank"))
        return None

    ra, rb = sr_for(side_a), sr_for(side_b)
    if ra is not None and rb is not None and ra != rb:
        return "a" if ra < rb else "b"
    ta = tb = 0
    for _g, q0, q1 in cluster:
        for q in (q0, q1):
            s = int(q.get("score") or 0)
            if _norm(q["name"]) == _norm(side_a):
                ta += s
            elif _norm(q["name"]) == _norm(side_b):
                tb += s
    if ta > tb:
        return "a"
    if tb > ta:
        return "b"
    return None


def _pair_match(
    cluster: list[tuple],
    *,
    key: str,
    label: str,
    phase: str,
    series_mode: str,
    walkover_bo3: bool = True,
) -> dict[str, Any]:
    first_gn, fp0, fp1 = cluster[0]
    side_a, side_b = fp0["name"], fp1["name"]
    id_a, id_b = as_str(fp0.get("id")), as_str(fp1.get("id"))
    walkover = False
    pin_games: list[list[int]] = []
    wins_a = wins_b = 0
    scratch_a = scratch_b = 0
    for _gn, p0, p1 in cluster:
        if as_str(p0.get("club")) == "KO_WO" or as_str(p1.get("club")) == "KO_WO":
            walkover = True
        if _is_bye(p0["name"]) or _is_bye(p1["name"]):
            walkover = True
        if p0["name"] == side_a:
            sa, sb = int(p0["score"]), int(p1["score"])
        elif p0["name"] == side_b:
            sa, sb = int(p1["score"]), int(p0["score"])
        else:
            sa, sb = int(p0["score"]), int(p1["score"])
        if not walkover and (sa > 0 or sb > 0):
            pin_games.append([sa, sb])
            scratch_a += sa
            scratch_b += sb
            if sa > sb:
                wins_a += 1
            elif sb > sa:
                wins_b += 1
    mode = _series_mode(series_mode, SERIES_BO3)
    scratch_series = scratch_final = False
    winner = None
    if walkover:
        wo_wins = 2 if walkover_bo3 else 1
        if not _is_bye(side_a) and _is_bye(side_b):
            wins_a, wins_b, winner = wo_wins, 0, "a"
        elif _is_bye(side_a) and not _is_bye(side_b):
            wins_a, wins_b, winner = 0, wo_wins, "b"
        else:
            wins_a, wins_b, winner = wo_wins, 0, "a"
    elif mode == SERIES_SCRATCH_2G and pin_games:
        scratch_series = True
        scratch_final = key == "F"
        if scratch_a > scratch_b:
            winner = "a"
        elif scratch_b > scratch_a:
            winner = "b"
        else:
            winner = _tiebreak(cluster, side_a, side_b)
    else:
        if wins_a > wins_b:
            winner = "a"
        elif wins_b > wins_a:
            winner = "b"
        elif pin_games and wins_a == wins_b and wins_a >= 1 and key != "F":
            tb = _tiebreak(cluster, side_a, side_b)
            if tb == "a":
                wins_a, wins_b, winner = wins_a + 1, wins_b, "a"
            elif tb == "b":
                wins_a, wins_b, winner = wins_a, wins_b + 1, "b"
    return {
        "key": key,
        "label": label,
        "phase": phase,
        "kind": "pair",
        "series_mode": mode,
        "side_a": {"name": _display_name(side_a), "id": id_a, "games_won": wins_a},
        "side_b": {"name": _display_name(side_b), "id": id_b, "games_won": wins_b},
        "pin_games": pin_games,
        "walkover": walkover,
        "winner": winner,
        "first_game_number": first_gn,
        "scratch_total_a": scratch_a,
        "scratch_total_b": scratch_b,
        "scratch_series": scratch_series,
        "scratch_final": scratch_final,
    }


def _tree_bracket(season: str, event: str, games: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    series = _finale_series(cfg)
    empty = empty_bracket(series=series, fmt=KO_TREE, basis=_decision_basis(cfg))
    ko_rows = _ko_games(games)
    if not ko_rows:
        return empty
    blocks: list[tuple[int, tuple[str, str], dict[str, Any], dict[str, Any]]] = []
    for gn, gdf in _games_by_number(ko_rows):
        players = _players_for_game(gdf, use_hdc=False)
        if len(players) < 2:
            continue
        if len(players) > 2:
            players = players[:2]
        p0, p1 = players[0], players[1]
        pair_key = tuple(sorted([p0["name"], p1["name"]], key=str.casefold))
        blocks.append((gn, pair_key, p0, p1))
    clusters = _pair_clusters(blocks)
    if not clusters:
        return empty
    keys = _match_keys(len(clusters))
    matches = []
    for idx, cluster in enumerate(clusters):
        mk = keys[idx] if idx < len(keys) else f"M{idx + 1}"
        match = _pair_match(
            cluster,
            key=mk,
            label="Finale" if mk == "F" else mk,
            phase=_tree_phase(idx, len(clusters)),
            series_mode=series,
            walkover_bo3=True,
        )
        matches.append(match)
    matches = _insert_inferred_sf2(matches)
    matches = _relabel(matches)
    matches = _resolve_sf_walkovers(matches)
    matches = _apply_overrides(season, event, matches)
    return {
        **empty,
        "matches": matches,
        "placements": _tree_placements(matches),
        **_path_meta(matches),
        "ko_finale_series": series,
        "ko_bracket_format": KO_TREE,
    }


def _insert_inferred_sf2(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(matches) != 4:
        return matches
    sf1, fin = matches[2], matches[3]
    s1 = {_norm(sf1["side_a"]["name"]), _norm(sf1["side_b"]["name"])}
    fa = as_str(fin["side_a"]["name"])
    fb = as_str(fin["side_b"]["name"])
    adv = next((cand for cand in (fa, fb) if _norm(cand) not in s1), None)
    if not adv or _is_bye(adv):
        return matches
    adv_id = as_str(fin["side_a"].get("id")) if _norm(adv) == _norm(fa) else as_str(fin["side_b"].get("id"))
    syn = {
        "key": "SF2",
        "label": "SF2",
        "phase": "sf",
        "kind": "pair",
        "side_a": {"name": adv, "id": adv_id, "games_won": 2},
        "side_b": {"name": "Nicht angetreten (No show)", "id": "", "games_won": 0},
        "pin_games": [],
        "walkover": True,
        "winner": "a",
        "first_game_number": int(fin.get("first_game_number") or 0),
        "inferred": True,
        "scratch_total_a": 0,
        "scratch_total_b": 0,
        "scratch_series": False,
        "scratch_final": False,
    }
    return matches[:3] + [syn] + [fin]


def _relabel(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = _match_keys(len(matches))
    for i, match in enumerate(matches):
        if i < len(keys):
            mk = keys[i]
            match["key"] = mk
            match["label"] = "Finale" if mk == "F" else mk
            match["phase"] = _tree_phase(i, len(matches))
    return matches


def _sf_placeholder(name: str) -> bool:
    base = _strip_no_show(name)
    return (not base) or _norm(base) == _norm("Nicht angetreten")


def _resolve_sf_walkovers(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {as_str(m.get("key")): m for m in matches}
    qf1, qf2 = by_key.get("QF1"), by_key.get("QF2")
    if not qf1 or not qf2:
        return matches
    w1, w2 = _winner_name(qf1), _winner_name(qf2)
    if not w1 or not w2:
        return matches
    w1b, w2b = _strip_no_show(w1), _strip_no_show(w2)

    def other_real(sf_key: str) -> set[str]:
        other = by_key.get("SF2" if sf_key == "SF1" else "SF1")
        if not other:
            return set()
        out = set()
        for side in ("side_a", "side_b"):
            nm = as_str((other.get(side) or {}).get("name"))
            if not _is_bye(nm):
                out.add(_norm(_strip_no_show(nm)))
        return out

    def apply(sf_key: str) -> None:
        sm = by_key.get(sf_key)
        if not sm or sm.get("inferred"):
            return
        if not (sm.get("walkover") or _sf_placeholder(as_str((sm.get("side_a") or {}).get("name"))) or _sf_placeholder(as_str((sm.get("side_b") or {}).get("name")))):
            return
        sa, sb = sm["side_a"], sm["side_b"]
        na, nb = as_str(sa.get("name")), as_str(sb.get("name"))
        if _sf_placeholder(na) and not _sf_placeholder(nb):
            bye_dict, present = sa, sb
        elif _sf_placeholder(nb) and not _sf_placeholder(na):
            bye_dict, present = sb, sa
        else:
            return
        present_clean = _strip_no_show(as_str(present.get("name")))
        np = _norm(present_clean)
        other = other_real(sf_key)
        candidates = []
        for wb in (w1b, w2b):
            nwb = _norm(wb)
            if nwb and nwb != np and nwb not in other:
                candidates.append(wb)
        absent = None
        if len(candidates) == 1:
            absent = candidates[0]
        elif len(candidates) == 2:
            if np == _norm(w1b):
                absent = w2b
            elif np == _norm(w2b):
                absent = w1b
        if not absent:
            return
        bye_dict["name"] = f"{absent} (No show)"
        target = _norm(absent)
        for qm in (qf1, qf2):
            for side in ("side_a", "side_b"):
                s = qm.get(side) or {}
                if _norm(_strip_no_show(as_str(s.get("name")))) == target:
                    if s.get("id"):
                        bye_dict["id"] = s["id"]
                    return

    apply("SF1")
    apply("SF2")
    return matches


def _apply_overrides(season: str, event: str, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw = _load_overrides()
    block = raw.get(f"{season.strip()}||{event.strip()}")
    if not isinstance(block, dict):
        group = normalize_tournament_group_name(event)
        for key, value in raw.items():
            if "||" not in str(key):
                continue
            s, name = str(key).split("||", 1)
            if s.strip() == season.strip() and normalize_tournament_group_name(name) == group:
                block = value
                break
    if not isinstance(block, dict):
        return matches
    rep = block.get("replace_sf_bye") or {}
    if not isinstance(rep, dict):
        return matches
    by_key = {as_str(m.get("key")): m for m in matches}
    for sf_key, spec in rep.items():
        if not isinstance(spec, dict):
            continue
        sm = by_key.get(str(sf_key))
        if not sm:
            continue
        name = as_str(spec.get("name"))
        if not name:
            continue
        display = name if "(no show)" in name.lower() else f"{name} (No show)"
        for side in ("side_a", "side_b"):
            sd = dict(sm.get(side) or {})
            if not _sf_placeholder(as_str(sd.get("name"))):
                continue
            sd["name"] = display
            if spec.get("id"):
                sd["id"] = as_str(spec.get("id"))
            sm[side] = sd
            break
    return matches


def _tree_placements(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def loser(m: dict[str, Any]) -> tuple[str, str]:
        if m.get("winner") == "a":
            return as_str((m.get("side_b") or {}).get("name")), as_str((m.get("side_b") or {}).get("id"))
        if m.get("winner") == "b":
            return as_str((m.get("side_a") or {}).get("name")), as_str((m.get("side_a") or {}).get("id"))
        return "", ""

    def winner(m: dict[str, Any]) -> tuple[str, str]:
        if m.get("winner") == "a":
            return as_str((m.get("side_a") or {}).get("name")), as_str((m.get("side_a") or {}).get("id"))
        if m.get("winner") == "b":
            return as_str((m.get("side_b") or {}).get("name")), as_str((m.get("side_b") or {}).get("id"))
        return "", ""

    by_key = {as_str(m.get("key")): m for m in matches}
    out: list[dict[str, Any]] = []
    fin = by_key.get("F")
    if not fin:
        return out
    wn, wid = winner(fin)
    ln, lid = loser(fin)
    if wn and not _is_bye(wn):
        out.append({"place": 1, "player": wn, "player_id": wid})
    if ln and not _is_bye(ln):
        out.append({"place": 2, "player": ln, "player_id": lid})
    for sk, place in (("SF1", 3), ("SF2", 3), ("QF1", 5), ("QF2", 5)):
        sm = by_key.get(sk)
        if not sm:
            continue
        lost, pid = loser(sm)
        if lost and not _is_bye(lost):
            out.append({"place": place, "player": lost, "player_id": pid})
    return out


def _path_meta(matches: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {as_str(m.get("key")): m for m in matches}
    fin = by_key.get("F")
    if not fin:
        return {
            "finalist_a": None,
            "finalist_b": None,
            "path_keys_a": [],
            "path_keys_b": [],
            "palette_index_a": 2,
            "palette_index_b": 8,
        }
    fa = as_str((fin.get("side_a") or {}).get("name"))
    fb = as_str((fin.get("side_b") or {}).get("name"))

    def in_match(m: dict[str, Any], nc: str) -> bool:
        if m.get("kind") == "field":
            return any(_norm(_strip_no_show(as_str(p.get("name")))) == nc for p in (m.get("field") or []) if isinstance(p, dict))
        sides = {
            _norm(_strip_no_show(as_str((m.get("side_a") or {}).get("name")))),
            _norm(_strip_no_show(as_str((m.get("side_b") or {}).get("name")))),
        }
        return nc in sides

    def match_by_key(sk: str) -> dict[str, Any] | None:
        if sk in by_key:
            return by_key[sk]
        parent = by_key.get("ELIM")
        if not parent:
            return None
        for row in parent.get("rounds") or []:
            if isinstance(row, dict) and as_str(row.get("key")) == sk:
                return row
        return None

    def path_for(name: str) -> list[str]:
        nc = _norm(_strip_no_show(name))
        if not nc or _is_bye(name):
            return ["F"]
        path = ["F"]
        for sk in ("SL2", "SL1", "SF2", "SF1", "QF2", "QF1", "ELIM2", "ELIM1", "ELIM"):
            sm = match_by_key(sk)
            if sm and in_match(sm, nc):
                path.insert(0, sk)
        return path

    return {
        "finalist_a": fa,
        "finalist_b": fb,
        "path_keys_a": path_for(fa),
        "path_keys_b": path_for(fb),
        "palette_index_a": 2,
        "palette_index_b": 8,
    }


def _elim_phase(round_name: str) -> str:
    low = str(round_name or "").casefold()
    if "eliminierung" in low or re.search(r"\belim\b", low):
        return "elim"
    if "stepladder" in low or "step-ladder" in low or "step ladder" in low:
        return "stepladder"
    if "finale" in low or re.search(r"\bfinal\b", low):
        return "final"
    return "other"


def _elim_stepladder(season: str, event: str, games: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    phases = cfg.get("ko_finale_phases") if isinstance(cfg.get("ko_finale_phases"), dict) else {}
    basis = _decision_basis(cfg)
    use_hdc = basis == "handicap"
    empty = empty_bracket(series=_finale_series(cfg), fmt=KO_STEPLADDER, basis=basis)
    ko_rows = _ko_games(games)
    if not ko_rows:
        return empty
    tagged = []
    for row in ko_rows:
        item = dict(row)
        item["_phase"] = _elim_phase(as_str(row.get("round_name")))
        tagged.append(item)

    elim_cfg = phases.get("elim") if isinstance(phases.get("elim"), dict) else {}
    sl_cfg = phases.get("stepladder") if isinstance(phases.get("stepladder"), dict) else {}
    final_cfg = phases.get("final") if isinstance(phases.get("final"), dict) else {}
    place_last = int(elim_cfg.get("place_last") or 6)
    place_second_last = int(elim_cfg.get("place_second_last") or 5)
    sl_specs = [s for s in (sl_cfg.get("matches") or []) if isinstance(s, dict)]
    sl_mode = _series_mode(sl_cfg.get("series_mode"), SERIES_SINGLE)
    final_mode = _series_mode(final_cfg.get("series_mode"), SERIES_BO3)
    final_key = as_str(final_cfg.get("key") or "F")
    final_label = as_str(final_cfg.get("label_de") or "Finale")
    matches_out: list[dict[str, Any]] = []

    elim_rows = [row for row in tagged if row["_phase"] == "elim"]
    if not elim_rows:
        field_names = None
        elim_gns: list[int] = []
        for gn, gdf in _games_by_number(tagged):
            players = _players_for_game(gdf, use_hdc=use_hdc)
            names = {p["name"].casefold() for p in players}
            if field_names is None:
                if len(players) < 3:
                    continue
                field_names = names
                elim_gns.append(gn)
            elif names.issubset(field_names) and len(players) >= 2:
                elim_gns.append(gn)
                field_names = names
            else:
                break
        elim_rows = [row for row in tagged if as_int(row.get("game_number")) in set(elim_gns)]

    if elim_rows:
        elim_games = []
        for gn, gdf in _games_by_number(elim_rows):
            players = _players_for_game(gdf, use_hdc=use_hdc)
            if len(players) >= 2:
                elim_games.append((gn, players))
        if elim_games:
            remaining = {p["name"].casefold() for p in elim_games[0][1]}
            place_cursor = place_last
            elim_rounds = []
            for game_i, (gn, players) in enumerate(elim_games, start=1):
                active = [p for p in players if p["name"].casefold() in remaining]
                if len(active) < 2:
                    continue
                out_p = min(active, key=lambda p: (int(p["score"]), str(p["name"]).casefold()))
                out_key = out_p["name"].casefold()
                remaining.discard(out_key)
                eliminated_place = place_cursor
                place_cursor = place_second_last if place_cursor == place_last else place_cursor - 1
                field_rows = []
                ordered = sorted(active, key=lambda x: (-int(x["score"]), str(x["name"]).casefold()))
                for rank_i, p in enumerate(ordered, start=1):
                    nk = p["name"].casefold()
                    eliminated = nk == out_key
                    row = {
                        "name": _display_name(str(p["name"])),
                        "id": as_str(p.get("id")),
                        "games": [int(p["score"])],
                        "total": int(p["score"]),
                        "rank": rank_i,
                        "advances": not eliminated,
                        "eliminated": eliminated,
                    }
                    if eliminated:
                        row["place"] = eliminated_place
                    field_rows.append(row)
                advancers = [r["name"] for r in field_rows if r.get("advances")]
                elim_rounds.append(
                    {
                        "key": f"ELIM{game_i}",
                        "label": f"Spiel {game_i}",
                        "phase": "elim",
                        "kind": "field",
                        "decision_basis": basis,
                        "field": field_rows,
                        "advancer": advancers[0] if len(advancers) == 1 else None,
                        "first_game_number": gn,
                        "side_a": {"name": advancers[0] if advancers else "—", "id": "", "games_won": 0},
                        "side_b": {"name": "—", "id": "", "games_won": 0},
                    }
                )
            if elim_rounds:
                last_adv = elim_rounds[-1].get("advancer") or ""
                flat_field = []
                seen: set[str] = set()
                for rnd in elim_rounds:
                    for p in rnd.get("field") or []:
                        nk = _fold(p.get("name"))
                        if nk in seen:
                            continue
                        if p.get("place") or p.get("eliminated"):
                            flat_field.append(p)
                            seen.add(nk)
                matches_out.append(
                    {
                        "key": as_str(elim_cfg.get("key") or "ELIM"),
                        "label": as_str(elim_cfg.get("label_de") or "Eliminierung"),
                        "phase": "elim",
                        "kind": "field",
                        "series_mode": as_str(elim_cfg.get("series_mode") or SERIES_SINGLE),
                        "decision_basis": basis,
                        "rounds": elim_rounds,
                        "field": flat_field,
                        "advancer": last_adv,
                        "side_a": {"name": last_adv or "—", "id": "", "games_won": 0},
                        "side_b": {"name": "—", "id": "", "games_won": 0},
                        "pin_games": [],
                        "walkover": False,
                        "winner": "a" if last_adv else None,
                        "first_game_number": elim_rounds[0].get("first_game_number"),
                        "scratch_total_a": 0,
                        "scratch_total_b": 0,
                        "scratch_series": False,
                        "scratch_final": False,
                    }
                )

    elim_gns = {as_int(row.get("game_number")) for row in elim_rows}
    pair_rows = [row for row in tagged if row["_phase"] in {"stepladder", "final"}]
    if not pair_rows:
        pair_rows = [row for row in tagged if as_int(row.get("game_number")) not in elim_gns]
    pair_blocks = []
    for gn, gdf in _games_by_number(pair_rows):
        players = _players_for_game(gdf, use_hdc=use_hdc)
        if len(players) < 2:
            continue
        if len(players) == 2:
            p0, p1 = players[0], players[1]
        else:
            ordered = sorted(players, key=lambda p: str(p["name"]).casefold())
            p0, p1 = ordered[0], ordered[1]
        pair_key = tuple(sorted([p0["name"], p1["name"]], key=str.casefold))
        pair_blocks.append((gn, pair_key, p0, p1))
    clusters = _pair_clusters(pair_blocks)
    phase_by_gn = {}
    for row in pair_rows:
        gn = as_int(row.get("game_number"))
        if gn is not None and gn not in phase_by_gn:
            phase_by_gn[gn] = row.get("_phase")
    pair_keys: list[str] = []
    sl_i = 0
    for c_i, cluster in enumerate(clusters):
        first_gn = cluster[0][0]
        hint = str(phase_by_gn.get(first_gn) or "")
        is_last = c_i == len(clusters) - 1
        if hint == "final" or (sl_i >= len(sl_specs) and is_last):
            pair_keys.append(final_key)
        elif sl_i < len(sl_specs):
            pair_keys.append(as_str(sl_specs[sl_i].get("key") or f"SL{sl_i + 1}"))
            sl_i += 1
        else:
            pair_keys.append(f"SL{sl_i + 1}")
            sl_i += 1
    if len(clusters) > len(sl_specs) and pair_keys and pair_keys[-1] != final_key:
        pair_keys[-1] = final_key
    for c_idx, cluster in enumerate(clusters):
        mk = pair_keys[c_idx] if c_idx < len(pair_keys) else f"M{c_idx + 1}"
        is_final = mk == final_key or mk == "F"
        mode = final_mode if is_final else sl_mode
        label = final_label if is_final else next(
            (as_str(s.get("label_de") or mk) for s in sl_specs if as_str(s.get("key")) == mk),
            mk,
        )
        match = _pair_match(
            cluster,
            key=mk,
            label=label,
            phase="final" if is_final else "stepladder",
            series_mode=mode,
            walkover_bo3=is_final,
        )
        match["decision_basis"] = basis
        if is_final:
            loser_place = int(final_cfg.get("loser_place") or 2)
            winner_place = int(final_cfg.get("winner_place") or 1)
            match["loser_place"] = loser_place
            match["winner_place"] = winner_place
            if match.get("winner") == "a":
                match["side_a"]["place"] = winner_place
                match["side_b"]["place"] = loser_place
            elif match.get("winner") == "b":
                match["side_b"]["place"] = winner_place
                match["side_a"]["place"] = loser_place
        else:
            loser_place = None
            for spec in sl_specs:
                if as_str(spec.get("key")) == mk:
                    loser_place = int(spec.get("loser_place") or 0) or None
                    break
            if loser_place:
                match["loser_place"] = loser_place
                if match.get("winner") == "a":
                    match["side_b"]["place"] = loser_place
                elif match.get("winner") == "b":
                    match["side_a"]["place"] = loser_place
        matches_out.append(match)

    return {
        **empty,
        "matches": matches_out,
        "placements": _stepladder_placements(matches_out, phases),
        **_path_meta(matches_out),
        "ko_finale_series": final_mode,
        "ko_bracket_format": KO_STEPLADDER,
        "ko_decision_basis": basis,
    }


def _stepladder_placements(matches: list[dict[str, Any]], phases: dict[str, Any]) -> list[dict[str, Any]]:
    def loser(m: dict[str, Any]) -> tuple[str, str]:
        if m.get("winner") == "a":
            return as_str((m.get("side_b") or {}).get("name")), as_str((m.get("side_b") or {}).get("id"))
        if m.get("winner") == "b":
            return as_str((m.get("side_a") or {}).get("name")), as_str((m.get("side_a") or {}).get("id"))
        return "", ""

    def winner(m: dict[str, Any]) -> tuple[str, str]:
        if m.get("winner") == "a":
            return as_str((m.get("side_a") or {}).get("name")), as_str((m.get("side_a") or {}).get("id"))
        if m.get("winner") == "b":
            return as_str((m.get("side_b") or {}).get("name")), as_str((m.get("side_b") or {}).get("id"))
        return "", ""

    by_key = {as_str(m.get("key")): m for m in matches}
    out: list[dict[str, Any]] = []
    elim_cfg = phases.get("elim") if isinstance(phases.get("elim"), dict) else {}
    sl_cfg = phases.get("stepladder") if isinstance(phases.get("stepladder"), dict) else {}
    final_cfg = phases.get("final") if isinstance(phases.get("final"), dict) else {}
    final_key = as_str(final_cfg.get("key") or "F")
    fin = by_key.get(final_key) or by_key.get("F")
    if fin:
        wn, wid = winner(fin)
        ln, lid = loser(fin)
        if wn and not _is_bye(wn):
            out.append({"place": int(final_cfg.get("winner_place") or 1), "player": wn, "player_id": wid})
        if ln and not _is_bye(ln):
            out.append({"place": int(final_cfg.get("loser_place") or 2), "player": ln, "player_id": lid})
    sl_matches = sl_cfg.get("matches") if isinstance(sl_cfg.get("matches"), list) else []
    for spec in reversed(sl_matches):
        if not isinstance(spec, dict):
            continue
        sm = by_key.get(as_str(spec.get("key")))
        if not sm:
            continue
        lost, lid = loser(sm)
        if lost and not _is_bye(lost):
            out.append({"place": int(spec.get("loser_place") or 0), "player": lost, "player_id": lid})
    elim_key = as_str(elim_cfg.get("key") or "ELIM")
    elim_matches = [
        m
        for m in matches
        if m.get("phase") == "elim" or m.get("kind") == "field" or as_str(m.get("key")).upper().startswith("ELIM")
    ]
    if not elim_matches and by_key.get(elim_key):
        elim_matches = [by_key[elim_key]]
    for elim in elim_matches:
        if elim.get("kind") != "field":
            continue
        nested = [r for r in (elim.get("rounds") or []) if isinstance(r, dict)]
        fields = [r.get("field") or [] for r in nested] if nested else [elim.get("field") or []]
        for field in fields:
            for p in field:
                if not isinstance(p, dict) or not p.get("place"):
                    continue
                nm = as_str(p.get("name"))
                if nm and not _is_bye(nm):
                    out.append({"place": int(p["place"]), "player": nm, "player_id": as_str(p.get("id"))})
    out.sort(key=lambda r: int(r.get("place") or 99))
    seen: set[int] = set()
    deduped = []
    for row in out:
        pl = int(row.get("place") or 0)
        if pl in seen:
            continue
        seen.add(pl)
        deduped.append(row)
    return deduped
