"""Player search, lifetime stats, highlights, tournament results."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine import (
    Query,
    abs_,
    avg_,
    count_star,
    fetch_dicts,
    fetch_one,
    game_line,
    lower,
    max_,
    min_,
    round_,
    sum_,
    tournament_line,
    trim,
)
from bowlyzerapi.queries.filters import is_bye, is_league_fact, is_player_game
from bowlyzerapi.queries.tournament import tournament_section
from bowlyzerapi.queries.util import as_float, as_int, as_str
from bowlyzerapi.warehouse import session


def _resolve_player(con, ident: str) -> tuple[str | None, str | None]:
    """Return (player_id, player_name) from id or name."""
    g = game_line
    ident = (ident or "").strip()
    if not ident:
        return None, None
    by_id = fetch_one(
        con,
        Query()
        .from_(g)
        .select(g.player_id, g.player_name)
        .where(g.player_id == ident, ~is_bye(g.player_name))
        .limit(1),
    )
    if by_id:
        return as_str(by_id[0]), as_str(by_id[1])
    by_name = fetch_one(
        con,
        Query()
        .from_(g)
        .select(g.player_id, g.player_name)
        .where(lower(trim(g.player_name)) == lower(trim(ident)), ~is_bye(g.player_name))
        .limit(1),
    )
    if by_name:
        return as_str(by_name[0]), as_str(by_name[1])
    t = tournament_line
    trow = fetch_one(
        con,
        Query()
        .from_(t)
        .select(t.player_id, t.player_name)
        .where((t.player_id == ident) | (lower(trim(t.player_name)) == lower(trim(ident))), ~is_bye(t.player_name))
        .limit(1),
    )
    if trow:
        return as_str(trow[0]), as_str(trow[1])
    return None, ident


def search_players(*, q: str | None = None, club: str | None = None, limit: int = 50) -> dict[str, Any]:
    g = game_line
    t = tournament_line
    limit = max(1, min(int(limit), 200))
    needle = f"%{q}%" if q else None

    def _q(table, extra):
        query = (
            Query()
            .from_(table)
            .select(table.player_id, table.player_name.as_("name"))
            .where(~is_bye(table.player_name), table.player_id.is_not_null(), *extra)
            .distinct()
        )
        if club:
            query = query.where(lower(trim(table.club)) == lower(trim(club)))
        if needle:
            query = query.where(table.player_name.ilike(needle) | table.player_id.ilike(needle))
        return query

    with session() as con:
        rows = fetch_dicts(con, _q(g, [is_player_game(g)])) + fetch_dicts(con, _q(t, []))
    seen: set[tuple[str, str]] = set()
    players = []
    for row in sorted(rows, key=lambda r: (str(r["name"] or ""), str(r["player_id"] or ""))):
        key = (str(row["player_id"] or ""), str(row["name"] or ""))
        if key in seen:
            continue
        seen.add(key)
        players.append({"id": as_str(row["player_id"]), "name": as_str(row["name"])})
        if len(players) >= limit:
            break
    return {"q": q, "club": club, "players": players}


def player_document(ident: str, *, club: str | None = None, season: str | None = None) -> dict[str, Any]:
    g = game_line
    t = tournament_line
    with session() as con:
        player_id, player_name = _resolve_player(con, ident)
        if not player_id and not player_name:
            return {"player_id": None, "player_name": ident, "seasons": [], "lifetime": {}, "highlights": []}
        who_g = (g.player_id == player_id) if player_id else (lower(trim(g.player_name)) == lower(trim(player_name)))
        who_t = (t.player_id == player_id) if player_id else (lower(trim(t.player_name)) == lower(trim(player_name)))
        if club:
            who_g = who_g & (lower(trim(g.club)) == lower(trim(club)))
            who_t = who_t & (lower(trim(t.club)) == lower(trim(club)))
        league_q = (
            Query()
            .from_(g)
            .select(
                g.season,
                g.event,
                g.club,
                g.team,
                count_star().as_("games"),
                sum_(abs_(g.score)).as_("pins"),
                round_(avg_(abs_(g.score)), 2).as_("average"),
                max_(abs_(g.score)).as_("best_game"),
                min_(abs_(g.score)).as_("worst_game"),
            )
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), who_g)
            .group_by(g.season, g.event, g.club, g.team)
            .order_by(g.season, g.event)
        )
        if season and season != "all":
            league_q = league_q.where(g.season == season)
        league_rows = fetch_dicts(con, league_q)
        tourney_q = (
            Query()
            .from_(t)
            .select(
                t.season,
                t.event,
                t.club,
                count_star().as_("games"),
                sum_(t.score).as_("pins"),
                round_(avg_(t.score), 2).as_("average"),
                max_(t.score).as_("best_game"),
                min_(t.score).as_("worst_game"),
            )
            .where(~is_bye(t.player_name), who_t)
            .group_by(t.season, t.event, t.club)
            .order_by(t.season, t.event)
        )
        if season and season != "all":
            tourney_q = tourney_q.where(t.season == season)
        tourney_rows = fetch_dicts(con, tourney_q)
        highlights = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.player_name,
                g.player_id,
                g.score,
                g.game_date,
                g.season,
                g.event,
                g.club,
                g.team,
                g.week,
                g.round_number,
            )
            .where(is_player_game(g), ~is_bye(g.player_name), who_g, g.score.is_not_null())
            .order_by(g.score.desc(), g.game_date.desc())
            .limit(10),
        )

    seasons = []
    for row in league_rows:
        seasons.append(
            {
                "season": as_str(row["season"]),
                "competition": as_str(row["event"]),
                "event_type": "league",
                "club": as_str(row["club"]),
                "team": as_str(row["team"]),
                "games": as_int(row["games"]),
                "pins": as_float(row["pins"]),
                "average": as_float(row["average"]),
            }
        )
    for row in tourney_rows:
        seasons.append(
            {
                "season": as_str(row["season"]),
                "competition": as_str(row["event"]),
                "event_type": "tournament",
                "club": as_str(row["club"]),
                "team": None,
                "games": as_int(row["games"]),
                "pins": as_float(row["pins"]),
                "average": as_float(row["average"]),
            }
        )
    games = sum(r["games"] or 0 for r in seasons)
    pins = sum(r["pins"] or 0 for r in seasons)
    season_avgs = [(r["season"], r["average"]) for r in seasons if r["event_type"] == "league" and r["average"]]
    best_season = max(season_avgs, key=lambda x: x[1]) if season_avgs else None
    return {
        "player_id": player_id,
        "player_name": player_name,
        "seasons": sorted(set(as_str(r["season"]) or "" for r in seasons)),
        "competitions": seasons,
        "lifetime": {
            "games": games,
            "pins": pins,
            "average": round(pins / games, 2) if games else None,
            "best_game": max((as_int(r["score"]) or 0 for r in highlights), default=None),
            "best_season": best_season[0] if best_season else None,
        },
        "highlights": [
            {
                "score": as_int(r["score"]),
                "date": as_str(r["game_date"]),
                "season": as_str(r["season"]),
                "competition": as_str(r["event"]),
                "club": as_str(r["club"]),
                "team": as_str(r["team"]),
                "week": as_int(r["week"]),
                "round": as_int(r["round_number"]),
            }
            for r in highlights
        ],
    }


def player_tournaments(ident: str) -> dict[str, Any]:
    t = tournament_line
    with session() as con:
        player_id, player_name = _resolve_player(con, ident)
        if not player_name and not player_id:
            return {"player_id": None, "player_name": ident, "results": []}
        who = (t.player_id == player_id) if player_id else (lower(trim(t.player_name)) == lower(trim(player_name)))
        pairs = fetch_dicts(
            con,
            Query()
            .from_(t)
            .select(t.season, t.event, t.club, round_(avg_(t.score), 2).as_("average"))
            .where(~is_bye(t.player_name), who)
            .group_by(t.season, t.event, t.club)
            .order_by(t.season.desc(), t.event),
        )
    results = []
    for row in pairs:
        section = tournament_section(str(row["season"]), str(row["event"]))
        lb = next(
            (
                r
                for r in section["leaderboard"]
                if (player_id and r.get("player_id") == player_id)
                or (player_name and r.get("player_name") == player_name)
            ),
            None,
        )
        results.append(
            {
                "season": as_str(row["season"]),
                "tournament": as_str(row["event"]),
                "position": lb["rank"] if lb else None,
                "average": as_float(row["average"]),
                "club": as_str(row["club"]),
            }
        )
    return {"player_id": player_id, "player_name": player_name, "results": results}
