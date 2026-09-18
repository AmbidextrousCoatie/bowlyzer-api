"""Team document: batched league snapshots vs per-league standings."""

from __future__ import annotations

import time

import pytest

from bowlyzerapi.warehouse import session, warehouse_path

TEAM = "EPA München 3"


def test_snapshots_match_league_standings() -> None:
    from bowlyzerapi.queries.league import league_standings, league_table_snapshots
    from bowlyzerapi.queries.team import team_document

    if not warehouse_path().is_file():
        pytest.skip("no warehouse")

    t0 = time.perf_counter()
    doc = team_document(TEAM)
    doc_ms = (time.perf_counter() - t0) * 1000
    assert doc["history"]
    assert doc["leagues"]
    assert len(doc["history"]) == len(doc["leagues"])
    print(f"team_document({TEAM!r}) {doc_ms:.0f} ms seasons={len(doc['seasons'])}")
    assert doc_ms < 1500, f"team_document still too slow: {doc_ms:.0f} ms"

    pairs = [(s, block["league_name"]) for s, block in doc["history"].items()]
    with session() as con:
        snaps = league_table_snapshots(con, pairs)

    for season, league in pairs:
        expected = league_standings(season, league)["standings"]
        got = snaps[(season, league)]["standings"]
        assert [(r["rank"], r["team"], r["points"], r["pins"], r["games"], r["average"]) for r in got] == [
            (r["rank"], r["team"], r["points"], r["pins"], r["games"], r["average"]) for r in expected
        ]
        avgs = [r["average"] for r in expected if r["average"] is not None]
        field_avg = round(sum(avgs) / len(avgs), 2) if avgs else None
        assert snaps[(season, league)]["league_average"] == field_avg
        assert snaps[(season, league)]["num_teams"] == len(expected)

        team_row = next((r for r in expected if r["team"] == TEAM), None)
        hist = doc["history"][season]
        block = doc["leagues"][season]
        assert hist["league_name"] == league
        assert hist["final_position"] == (team_row["rank"] if team_row else None)
        assert block["final_position"] == hist["final_position"]
        assert block["num_teams"] == len(expected)
        assert block["team_average"] == (team_row["average"] if team_row else None)
        assert block["league_average"] == field_avg
