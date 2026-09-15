#!/usr/bin/env python
"""Compare DuckDB kernels to Flask TournamentService / LeagueService (in-process).

Run from bowlyzer_deploy so pandas adapters resolve published Parquet:

  cd C:\\Users\\cfell\\repositories\\bowlyzer_deploy
  uv run --with duckdb python ..\\bowlyzer-api\\scripts\\compare_queries.py

  uv run --with duckdb python ..\\bowlyzer-api\\scripts\\compare_queries.py \\
      --club "BC EMAX Unterföhring"
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = Path(os.environ.get("BOWLYZER_DEPLOY", API_ROOT.parent / "bowlyzer_deploy"))
os.environ.setdefault("WAREHOUSE_PATH", str(API_ROOT / "data" / "bowlyzer.duckdb"))
sys.path.insert(0, str(DEPLOY_ROOT))
sys.path.insert(0, str(API_ROOT))
os.chdir(DEPLOY_ROOT)

from bowlyzerapi.queries.club import club_history  # noqa: E402
from bowlyzerapi.queries.tournament import tournament_section  # noqa: E402
from app.services.league_service import LeagueService  # noqa: E402
from app.services.tournament_service import TournamentService  # noqa: E402
from data_access.schema import Columns  # noqa: E402
from data_access.text_norm import normalize_unicode_label  # noqa: E402


def _flatten_table_rows(table: Any) -> list[dict[str, Any]]:
    payload = table.to_dict() if hasattr(table, "to_dict") else table
    fields: list[str] = []
    for group in payload.get("columns") or []:
        for col in group.get("columns") or []:
            field = col.get("field")
            if field:
                fields.append(field)
    out = []
    for row in payload.get("data") or []:
        out.append({fields[i]: row[i] if i < len(row) else None for i in range(len(fields))})
    return out


def _norm_name(value: Any) -> str:
    return normalize_unicode_label(str(value or "").strip())


def compare_tournament(season: str, event: str) -> int:
    t0 = time.perf_counter()
    duck = tournament_section(season, event)
    duck_ms = (time.perf_counter() - t0) * 1000

    svc = TournamentService(database="db_tournament_regions_2026_gf")
    t0 = time.perf_counter()
    flask_lb = svc.get_leaderboard_table(season, event)
    flask_fp = svc.get_field_progress(season, event)
    flask_ms = (time.perf_counter() - t0) * 1000

    flask_rows = _flatten_table_rows(flask_lb)
    duck_by = {_norm_name(r["player_name"]): r for r in duck["leaderboard"]}
    flask_by = {_norm_name(r.get("player")): r for r in flask_rows}

    missing = [n for n in sorted(set(flask_by) - set(duck_by)) if n]
    extra = sorted(set(duck_by) - set(flask_by))
    pin_mismatches = 0
    rank_mismatches = 0
    compared = 0
    use_net = bool(duck.get("use_net"))
    for name, frow in flask_by.items():
        drow = duck_by.get(name)
        if not drow:
            continue
        compared += 1
        flask_pins = frow.get("total_net") if use_net and frow.get("total_net") is not None else frow.get("total_score")
        duck_pins = drow["total_pins"] if use_net else drow["total_scratch"]
        if flask_pins is not None and int(round(float(flask_pins))) != int(round(float(duck_pins))):
            pin_mismatches += 1
        if int(frow.get("rank") or 0) != int(drow["rank"]):
            rank_mismatches += 1

    flask_series = flask_fp.get("player_rank_series") or {}
    duck_series = duck["field_progress"].get("player_rank_series") or {}
    series_players = set(_norm_name(n) for n in flask_series) | set(_norm_name(n) for n in duck_series)
    duck_series_n = {_norm_name(k): v for k, v in duck_series.items()}
    flask_series_n = {_norm_name(k): v for k, v in flask_series.items()}
    series_mismatch_players = 0
    series_checked = 0
    last_rank_mismatch = 0
    for name in series_players:
        fs = flask_series_n.get(name) or []
        ds = duck_series_n.get(name) or []
        n = min(len(fs), len(ds))
        if n == 0:
            series_mismatch_players += 1
            continue
        series_checked += 1
        if fs[:n] != ds[:n]:
            series_mismatch_players += 1
        if fs and ds and fs[min(len(fs), len(ds)) - 1] != ds[min(len(fs), len(ds)) - 1]:
            last_rank_mismatch += 1

    print("=== tournament section kernel ===")
    print(f"season={season!r} event={event!r} use_net={use_net}")
    print(f"duckdb {duck_ms:.1f} ms  flask {flask_ms:.1f} ms  (leaderboard+field_progress)")
    print(f"leaderboard flask={len(flask_by)} duck={len(duck_by)} compared={compared}")
    print(f"  missing_in_duck={len(missing)} extra_in_duck={len(extra)}")
    print(f"  pin_mismatches={pin_mismatches} rank_mismatches={rank_mismatches}")
    print(
        f"field_progress flask_slots={len(next(iter(flask_series.values()), []))} "
        f"duck_slots={duck['field_progress'].get('slots')} "
        f"series_prefix_mismatches={series_mismatch_players}/{series_checked} "
        f"final_rank_mismatches={last_rank_mismatch}"
    )
    if missing[:5]:
        print("  sample missing", missing[:5])
    if extra[:5]:
        print("  sample extra", extra[:5])

    ok = (
        not missing
        and pin_mismatches == 0
        and last_rank_mismatch == 0
    )
    print("RESULT", "PASS" if ok else "DIFF")
    return 0 if ok else 1


def compare_club(club: str) -> int:
    t0 = time.perf_counter()
    duck = club_history(club)
    duck_ms = (time.perf_counter() - t0) * 1000

    ls = LeagueService(database="db_real_merged")
    resolved = ls.resolve_club_name(club)
    t0 = time.perf_counter()
    flask = ls.get_club_team_season_matrix(resolved or club)
    flask_ms = (time.perf_counter() - t0) * 1000

    def _cell_map(payload: dict[str, Any]) -> dict[tuple[str, str], set[str]]:
        out: dict[tuple[str, str], set[str]] = {}
        for row in payload.get("rows") or []:
            team_no = str(row.get("team_number"))
            seasons = row.get("seasons") or {}
            for season, cell in seasons.items():
                items = cell.get("items") if isinstance(cell, dict) else None
                leagues = {str(it.get("league")) for it in (items or []) if it.get("league")}
                if not leagues and isinstance(cell, dict) and cell.get("leagues"):
                    leagues = {p.strip() for p in str(cell["leagues"]).split(",") if p.strip()}
                out[(team_no, str(season))] = leagues
        return out

    def _pos_map(payload: dict[str, Any]) -> dict[tuple[str, str, str], int | None]:
        out: dict[tuple[str, str, str], int | None] = {}
        for row in payload.get("rows") or []:
            team_no = str(row.get("team_number"))
            for season, cell in (row.get("seasons") or {}).items():
                items = cell.get("items") if isinstance(cell, dict) else None
                for it in items or []:
                    league = str(it.get("league") or "")
                    out[(team_no, str(season), league)] = it.get("final_position")
        return out

    d_cells = _cell_map(duck)
    f_cells = _cell_map(flask)
    keys = set(d_cells) | set(f_cells)
    league_mismatch = 0
    for key in keys:
        if d_cells.get(key, set()) != f_cells.get(key, set()):
            league_mismatch += 1

    d_pos = _pos_map(duck)
    f_pos = _pos_map(flask)
    pos_keys = set(d_pos) | set(f_pos)
    pos_mismatch = 0
    for key in pos_keys:
        if d_pos.get(key) != f_pos.get(key):
            pos_mismatch += 1

    print("=== club history ===")
    print(f"club in={club!r} duck={duck.get('club')!r} flask={flask.get('club')!r}")
    print(f"duckdb {duck_ms:.1f} ms  flask {flask_ms:.1f} ms")
    print(f"seasons duck={len(duck.get('seasons') or [])} flask={len(flask.get('seasons') or [])}")
    print(f"team-season cells={len(keys)} league_set_mismatches={league_mismatch}")
    print(f"position cells={len(pos_keys)} position_mismatches={pos_mismatch}")
    if pos_mismatch:
        for key in sorted(pos_keys):
            if d_pos.get(key) != f_pos.get(key):
                print("  position", key, "duck", d_pos.get(key), "flask", f_pos.get(key))
                break
    ok = league_mismatch == 0 and pos_mismatch == 0
    print("RESULT", "PASS" if ok else "DIFF")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="25/26")
    parser.add_argument("--event", default="Bayerische Meisterschaft - Männer Einzel")
    parser.add_argument("--club", default="BC EMAX Unterföhring")
    parser.add_argument("--skip-tournament", action="store_true")
    parser.add_argument("--skip-club", action="store_true")
    args = parser.parse_args()

    rc = 0
    if not args.skip_tournament:
        rc |= compare_tournament(args.season, args.event)
    if not args.skip_club:
        rc |= compare_club(args.club)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
