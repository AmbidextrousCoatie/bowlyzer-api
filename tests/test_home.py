"""Home landing counts: player games only, league + tournament."""

from __future__ import annotations

import pytest

from bowlyzerapi.warehouse import warehouse_path


def test_home_counts_player_games_and_tournament_only_ids() -> None:
    from bowlyzerapi.engine import Query, count_distinct, count_star, fetch_scalar, game_line, tournament_line
    from bowlyzerapi.queries.filters import is_bye, is_league_fact, is_player_game
    from bowlyzerapi.queries.home import home
    from bowlyzerapi.warehouse import session

    if not warehouse_path().is_file():
        pytest.skip("no warehouse")

    counts = home()["counts"]
    g = game_line
    t = tournament_line
    with session() as con:
        league_player = fetch_scalar(
            con,
            Query()
            .from_(g)
            .select(count_star())
            .where(is_league_fact(g), is_player_game(g), g.score.is_not_null(), ~is_bye(g.player_name)),
        )
        team_totals = fetch_scalar(
            con,
            Query().from_(g).select(count_star()).where(g.score.is_not_null(), g.computed_data.is_true()),
        )
        league_only_players = fetch_scalar(
            con,
            Query()
            .from_(g)
            .select(count_distinct(g.player_id))
            .where(is_player_game(g), g.player_id.is_not_null(), ~is_bye(g.player_name)),
        )

    assert counts["league_games"] == league_player
    assert counts["games"] == counts["league_games"] + counts["tournament_games"]
    assert counts["games"] < (league_player or 0) + (team_totals or 0)
    assert counts["players"] > (league_only_players or 0)
    assert counts["tournament_games"] > 0
