"""Player identity: include name-only historic games with the catalog id."""

from __future__ import annotations

import pytest

from bowlyzerapi.warehouse import warehouse_path


def test_hartfeil_player_includes_pre_id_donaubowler_games() -> None:
    from bowlyzerapi.queries.club import club_players
    from bowlyzerapi.queries.player import player_document, player_seasons

    if not warehouse_path().is_file():
        pytest.skip("no warehouse")

    club = club_players("Donaubowler Regensburg")
    rows = [
        row
        for row in club["players"]
        if "hartfeil" in str(row.get("player_name") or "").lower()
    ]
    assert rows, "expected Hartfeil on Donaubowler"
    club_games = max(int(row.get("games") or 0) for row in rows)
    assert club_games >= 1000

    ident = next((str(row.get("player_id") or "") for row in rows if row.get("player_id")), "") or "Hartfeil, Volkmar"
    doc = player_document(ident)
    lifetime_games = int((doc.get("lifetime") or {}).get("total_games") or 0)
    assert lifetime_games >= club_games

    seasons = player_seasons(ident=ident)["seasons"]
    assert any(str(season) < "10/11" for season in seasons)
    assert "08/09" in seasons
