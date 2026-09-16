"""Filter catalog: seasons, leagues, weeks, teams, rounds, tournaments."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine import Query, fetch_rows, game_line, lower, tournament_line, trim
from bowlyzerapi.queries.filters import event_scope, is_league_fact
from bowlyzerapi.warehouse import session


def catalog(
    *,
    season: str | None = None,
    league: str | None = None,
    week: int | None = None,
    club: str | None = None,
    team: str | None = None,
) -> dict[str, Any]:
    g = game_line
    t = tournament_line
    with session() as con:
        league_q = (
            Query()
            .from_(g)
            .select(g.season, g.event)
            .where(is_league_fact(g), g.event.is_not_null(), g.season.is_not_null())
        )
        if club:
            league_q = league_q.where(lower(trim(g.club)) == lower(trim(club)))
        if team:
            league_q = league_q.where(g.team == team)
        pairs = fetch_rows(con, league_q.distinct())

        seasons = sorted({str(s) for s, _e in pairs})
        leagues_by_season: dict[str, list[str]] = {}
        for s, e in pairs:
            leagues_by_season.setdefault(str(s), [])
            if str(e) not in leagues_by_season[str(s)]:
                leagues_by_season[str(s)].append(str(e))
        for names in leagues_by_season.values():
            names.sort()

        if season:
            league_names = leagues_by_season.get(season, [])
        else:
            league_names = sorted({str(e) for _s, e in pairs})

        weeks: list[int] = []
        teams: list[str] = []
        rounds: list[int] = []
        if season and league:
            weeks = [
                int(w)
                for (w,) in fetch_rows(
                    con,
                    Query()
                    .from_(g)
                    .select(g.week)
                    .where(event_scope(g, season, league), g.week.is_not_null())
                    .distinct()
                    .order_by(g.week),
                )
            ]
            teams = [
                str(n)
                for (n,) in fetch_rows(
                    con,
                    Query()
                    .from_(g)
                    .select(g.team)
                    .where(event_scope(g, season, league), g.team.is_not_null())
                    .distinct()
                    .order_by(g.team),
                )
            ]
            if week is not None:
                rounds = [
                    int(r)
                    for (r,) in fetch_rows(
                        con,
                        Query()
                        .from_(g)
                        .select(g.round_number)
                        .where(
                            event_scope(g, season, league),
                            g.week == week,
                            g.round_number.is_not_null(),
                        )
                        .distinct()
                        .order_by(g.round_number),
                    )
                ]

        tq = Query().from_(t).select(t.season, t.event).where(t.event.is_not_null()).distinct()
        if season:
            tq = tq.where(t.season == season)
        if club:
            tq = tq.where(lower(trim(t.club)) == lower(trim(club)))
        tourney_pairs = fetch_rows(con, tq)
        tournament_seasons = sorted({str(s) for s, _e in tourney_pairs if s})
        tournaments: list[str] = []
        seen: set[str] = set()
        for _s, event in sorted(tourney_pairs, key=lambda row: str(row[1])):
            name = str(event)
            if name not in seen:
                seen.add(name)
                tournaments.append(name)

        all_teams = [
            str(n)
            for (n,) in fetch_rows(
                con,
                Query()
                .from_(g)
                .select(g.team)
                .where(is_league_fact(g), g.team.is_not_null())
                .distinct()
                .order_by(g.team),
            )
        ]

    return {
        "seasons": seasons,
        "leagues": [{"short_name": name, "long_name": name, "value": name} for name in league_names],
        "leagues_by_season": leagues_by_season,
        "weeks": weeks,
        "teams": teams,
        "rounds": rounds,
        "tournament_seasons": tournament_seasons,
        "tournaments": tournaments,
        "all_teams": all_teams,
        "filters": {
            "season": season,
            "league": league,
            "week": week,
            "club": club,
            "team": team,
        },
    }
