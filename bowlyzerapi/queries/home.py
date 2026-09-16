"""Landing aggregate: counts + latest events."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine import (
    Query,
    count_distinct,
    count_star,
    fetch_dicts,
    fetch_scalar,
    game_line,
    tournament_line,
)
from bowlyzerapi.queries.filters import is_bye, is_league_fact, is_player_game
from bowlyzerapi.queries.util import as_int, as_str
from bowlyzerapi.warehouse import session


def home(*, limit: int = 8) -> dict[str, Any]:
    g = game_line
    t = tournament_line
    limit = max(1, min(int(limit), 50))
    with session() as con:
        league_games = as_int(
            fetch_scalar(
                con,
                Query()
                .from_(g)
                .select(count_star())
                .where(is_league_fact(g), is_player_game(g), g.score.is_not_null()),
            )
        ) or 0
        tournament_games = as_int(
            fetch_scalar(
                con,
                Query().from_(t).select(count_star()).where(t.score.is_not_null()),
            )
        ) or 0
        years = as_int(
            fetch_scalar(
                con,
                Query().from_(g).select(count_distinct(g.season)).where(g.season.is_not_null()),
            )
        ) or 0
        league_pairs = Query().from_(g).select(g.season, g.event).where(
            is_league_fact(g), g.season.is_not_null(), g.event.is_not_null()
        ).distinct().as_("lp")
        league_seasons = as_int(
            fetch_scalar(con, Query().from_(league_pairs).select(count_star()))
        ) or 0
        tournament_pairs = (
            Query()
            .from_(t)
            .select(t.season, t.event)
            .where(t.season.is_not_null(), t.event.is_not_null())
            .distinct()
            .as_("tp")
        )
        tournaments = as_int(
            fetch_scalar(con, Query().from_(tournament_pairs).select(count_star()))
        ) or 0
        players = as_int(
            fetch_scalar(
                con,
                Query()
                .from_(g)
                .select(count_distinct(g.player_id))
                .where(
                    is_player_game(g),
                    g.player_id.is_not_null(),
                    ~is_bye(g.player_name),
                ),
            )
        ) or 0
        latest = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.season, g.event, g.week, g.game_date)
            .where(
                is_league_fact(g),
                g.season.is_not_null(),
                g.event.is_not_null(),
                g.week.is_not_null(),
                g.game_date.is_not_null(),
            )
            .distinct()
            .order_by(g.game_date.desc(), g.season.desc(), g.week.desc())
            .limit(limit),
        )

    return {
        "counts": {
            "games": league_games + tournament_games,
            "league_games": league_games,
            "tournament_games": tournament_games,
            "years": years,
            "league_seasons": league_seasons,
            "tournaments": tournaments,
            "players": players,
        },
        "latest_events": [
            {
                "season": as_str(row["season"]),
                "league": as_str(row["event"]),
                "week": as_int(row["week"]),
                "date": as_str(row["game_date"]),
            }
            for row in latest
        ],
    }
