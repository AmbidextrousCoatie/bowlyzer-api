"""Season standings: batched tables + honor vs per-league documents."""

from __future__ import annotations

import time

import pytest

from bowlyzerapi.warehouse import warehouse_path

SEASON = "25/26"


def test_season_standings_match_league_tables() -> None:
    from bowlyzerapi.queries.league import league_standings, season_standings

    if not warehouse_path().is_file():
        pytest.skip("no warehouse")

    t0 = time.perf_counter()
    doc = season_standings(SEASON)
    doc_ms = (time.perf_counter() - t0) * 1000
    leagues = doc["leagues"]
    assert leagues
    print(f"season_standings({SEASON!r}) {doc_ms:.0f} ms leagues={len(leagues)}")
    assert doc_ms < 1500, f"season_standings still too slow: {doc_ms:.0f} ms"

    by_name = {row["league"]: row for row in leagues}
    for name, got in by_name.items():
        expected = league_standings(SEASON, name)
        assert got["week"] == expected["week"]
        assert [
            (r["rank"], r["team"], r["points"], r["pins"], r["games"], r["average"])
            for r in got["standings"]
        ] == [
            (r["rank"], r["team"], r["points"], r["pins"], r["games"], r["average"])
            for r in expected["standings"]
        ]
        assert got["honor_scores"] == expected["honor_scores"]
