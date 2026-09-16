"""Player search, lifetime stats, highlights, tournament results."""

from __future__ import annotations

import re
from typing import Any

from bowlyzerapi.engine import (
    Query,
    any_value,
    avg_,
    coalesce,
    count_star,
    fetch_dicts,
    fetch_one,
    game_line,
    lower,
    max_,
    min_,
    nullif,
    rank,
    relation,
    round_,
    sum_,
    tournament_line,
    trim,
    try_cast,
)
from bowlyzerapi.queries.filters import is_bye, is_league_fact, is_player_game, resolve_club
from bowlyzerapi.queries.identity import collapse_player_catalog, canonical_player_names
from bowlyzerapi.queries.tournament import tournament_section
from bowlyzerapi.queries.tournament_names import normalize_tournament_group_name
from bowlyzerapi.queries.util import as_float, as_int, as_str
from bowlyzerapi.warehouse import session

_TEAM_NUMBER = re.compile(r" (\d+)$")


def _team_number(team: str | None) -> int | None:
    if not team:
        return None
    match = _TEAM_NUMBER.search(team)
    return int(match.group(1)) if match else None


def _who(table, player_id: str | None, player_name: str | None):
    if player_id:
        return table.player_id == player_id
    return lower(trim(table.player_name)) == lower(trim(player_name or ""))


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
    pid: str | None = None
    pname: str | None = None
    if by_id:
        pid, pname = as_str(by_id[0]), as_str(by_id[1])
    else:
        by_name = fetch_one(
            con,
            Query()
            .from_(g)
            .select(g.player_id, g.player_name)
            .where(lower(trim(g.player_name)) == lower(trim(ident)), ~is_bye(g.player_name))
            .limit(1),
        )
        if by_name:
            pid, pname = as_str(by_name[0]), as_str(by_name[1])
        else:
            t = tournament_line
            trow = fetch_one(
                con,
                Query()
                .from_(t)
                .select(t.player_id, t.player_name)
                .where(
                    (t.player_id == ident) | (lower(trim(t.player_name)) == lower(trim(ident))),
                    ~is_bye(t.player_name),
                )
                .limit(1),
            )
            if trow:
                pid, pname = as_str(trow[0]), as_str(trow[1])
            else:
                return None, ident
    if pid:
        pname = canonical_player_names(con).get(pid, pname)
    return pid, pname


def _canonical_club(con, club: str | None) -> str | None:
    club = (club or "").strip() or None
    if not club:
        return None
    return resolve_club(con, club) or club


def search_players(*, q: str | None = None, club: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Player catalog. Empty ``q`` returns the full list (SPA fuzzy-filters)."""
    g = game_line
    t = tournament_line
    q = (q or "").strip() or None
    if limit is None:
        limit = 50 if q else 20_000
    limit = max(1, min(int(limit), 20_000))
    needle = f"%{q}%" if q else None

    with session() as con:
        canonical = _canonical_club(con, club)

        def catalog(table, extra):
            query = (
                Query()
                .from_(table)
                .select(table.player_id, table.player_name.as_("name"), count_star().as_("n"))
                .where(~is_bye(table.player_name), *extra)
                .group_by(table.player_id, table.player_name)
            )
            if canonical:
                query = query.where(lower(trim(table.club)) == lower(trim(canonical)))
            if needle:
                query = query.where(
                    table.player_name.ilike(needle) | try_cast(table.player_id, "VARCHAR").ilike(needle)
                )
            return query

        rows = fetch_dicts(con, catalog(g, [is_player_game(g)])) + fetch_dicts(con, catalog(t, []))
        names = canonical_player_names(con)

    players = collapse_player_catalog(rows)
    for player in players:
        canonical_name = names.get(player["id"])
        if not canonical_name:
            continue
        aliases = sorted({player["name"], *player["aliases"]} - {canonical_name})
        player["name"] = canonical_name
        player["aliases"] = aliases
    if limit:
        players = players[:limit]
    return {"q": q, "club": canonical, "players": players}


def player_seasons(*, ident: str | None = None, club: str | None = None) -> dict[str, Any]:
    g = game_line
    t = tournament_line
    ident = (ident or "").strip() or None
    with session() as con:
        canonical = _canonical_club(con, club)
        player_id = player_name = None
        if ident:
            player_id, player_name = _resolve_player(con, ident)
        league = Query().from_(g).select(g.season).where(is_player_game(g), ~is_bye(g.player_name), g.season.is_not_null())
        tourney = Query().from_(t).select(t.season).where(~is_bye(t.player_name), t.season.is_not_null())
        if ident:
            league = league.where(_who(g, player_id, player_name))
            tourney = tourney.where(_who(t, player_id, player_name))
        if canonical:
            league = league.where(lower(trim(g.club)) == lower(trim(canonical)))
            tourney = tourney.where(lower(trim(t.club)) == lower(trim(canonical)))
        rows = fetch_dicts(con, league.distinct()) + fetch_dicts(con, tourney.distinct())
    seasons = sorted({as_str(r["season"]) or "" for r in rows if as_str(r.get("season"))})
    return {"player_id": player_id if ident else None, "club": canonical, "seasons": seasons}


def player_highest_games(
    *,
    ident: str | None = None,
    club: str | None = None,
    season: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    limit = max(1, min(int(limit), 100))
    season = (season or "").strip() or None
    if season == "all":
        season = None
    ident = (ident or "").strip() or None
    g = game_line
    t = tournament_line
    with session() as con:
        canonical = _canonical_club(con, club)
        player_id = player_name = None
        if ident:
            player_id, player_name = _resolve_player(con, ident)
        league_q = (
            Query()
            .from_(g)
            .select(
                g.player_name,
                g.player_id,
                g.score,
                g.game_date,
                g.season,
                g.event.as_("competition"),
                g.club,
                g.team,
                g.week,
                g.round_number,
            )
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), g.score.is_not_null())
        )
        tourney_q = (
            Query()
            .from_(t)
            .select(
                t.player_name,
                t.player_id,
                t.score,
                t.game_date,
                t.season,
                t.event.as_("competition"),
                t.club,
                t.round_number,
            )
            .where(~is_bye(t.player_name), t.score.is_not_null())
        )
        if ident:
            league_q = league_q.where(_who(g, player_id, player_name))
            tourney_q = tourney_q.where(_who(t, player_id, player_name))
        if canonical:
            league_q = league_q.where(lower(trim(g.club)) == lower(trim(canonical)))
            tourney_q = tourney_q.where(lower(trim(t.club)) == lower(trim(canonical)))
        if season:
            league_q = league_q.where(g.season == season)
            tourney_q = tourney_q.where(t.season == season)
        league_q = league_q.order_by(g.score.desc(), g.game_date.desc()).limit(limit)
        tourney_q = tourney_q.order_by(t.score.desc(), t.game_date.desc()).limit(limit)
        league_rows = fetch_dicts(con, league_q)
        tourney_rows = fetch_dicts(con, tourney_q)
    games = [_highlight_game(row, is_tournament=False) for row in league_rows]
    games.extend(_highlight_game(row, is_tournament=True) for row in tourney_rows)
    games.sort(key=lambda row: (row.get("score") or 0, str(row.get("date") or "")), reverse=True)
    return {"games": games[:limit]}


def _highlight_game(row: dict[str, Any], *, is_tournament: bool) -> dict[str, Any]:
    team_name = as_str(row.get("team") or row.get("team_name"))
    return {
        "player_name": as_str(row.get("player_name")) or "",
        "player_id": as_str(row.get("player_id")) or "",
        "score": as_int(row.get("score")),
        "date": as_str(row.get("game_date") or row.get("date")),
        "season": as_str(row.get("season")),
        "competition": as_str(row.get("competition") or row.get("event")),
        "is_tournament": is_tournament,
        "club": as_str(row.get("club")),
        "team_name": team_name,
        "team_number": _team_number(team_name),
        "week": as_int(row.get("week")) if not is_tournament else None,
        "round_number": as_int(row.get("round_number")),
    }


def _empty_stats(ident: str | None = None, *, scope: str = "player") -> dict[str, Any]:
    return {
        "scope": scope,
        "player_id": None,
        "player_name": ident,
        "seasons_list": [],
        "lifetime": None,
        "seasons": [],
        "periods": [],
        "player_competitions": [],
        "player_season_totals": [],
        "highlights": [],
    }


def player_document(
    ident: str, *, club: str | None = None, season: str | None = None, top_n: int | None = None
) -> dict[str, Any]:
    ident = (ident or "").strip()
    if not ident:
        return player_aggregate(club=club, season=season, top_n=top_n)
    season = (season or "").strip() or None
    if season == "all":
        season = None
    g = game_line
    t = tournament_line
    with session() as con:
        canonical = _canonical_club(con, club)
        player_id, player_name = _resolve_player(con, ident)
        if not player_id and not player_name:
            return _empty_stats(ident)
        who_g = _who(g, player_id, player_name)
        who_t = _who(t, player_id, player_name)
        if canonical:
            who_g = who_g & (lower(trim(g.club)) == lower(trim(canonical)))
            who_t = who_t & (lower(trim(t.club)) == lower(trim(canonical)))
        league_comp = (
            Query()
            .from_(g)
            .select(
                g.season,
                g.event,
                g.club,
                g.team,
                count_star().as_("games"),
                sum_(g.score).as_("pins"),
                round_(avg_(g.score), 2).as_("average"),
                max_(g.score).as_("best_game"),
                min_(g.score).as_("worst_game"),
            )
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), who_g)
            .group_by(g.season, g.event, g.club, g.team)
        )
        tourney_comp = (
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
        )
        league_season = (
            Query()
            .from_(g)
            .select(
                g.season,
                count_star().as_("games"),
                sum_(g.score).as_("pins"),
                round_(avg_(g.score), 2).as_("average"),
                max_(g.score).as_("best_game"),
                min_(g.score).as_("worst_game"),
            )
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), who_g)
            .group_by(g.season)
        )
        tourney_season = (
            Query()
            .from_(t)
            .select(
                t.season,
                count_star().as_("games"),
                sum_(t.score).as_("pins"),
                round_(avg_(t.score), 2).as_("average"),
                max_(t.score).as_("best_game"),
                min_(t.score).as_("worst_game"),
            )
            .where(~is_bye(t.player_name), who_t)
            .group_by(t.season)
        )
        league_periods = (
            Query()
            .from_(g)
            .select(
                g.season,
                g.event.as_("competition"),
                g.week,
                g.club,
                g.team,
                count_star().as_("games"),
                round_(avg_(g.score), 2).as_("average"),
            )
            .where(
                is_league_fact(g),
                is_player_game(g),
                ~is_bye(g.player_name),
                who_g,
                g.week.is_not_null(),
            )
            .group_by(g.season, g.event, g.week, g.club, g.team)
        )
        tourney_periods = (
            Query()
            .from_(t)
            .select(
                t.season,
                t.event.as_("competition"),
                t.round_number,
                t.round_name,
                t.club,
                count_star().as_("games"),
                round_(avg_(t.score), 2).as_("average"),
            )
            .where(~is_bye(t.player_name), who_t, t.round_number.is_not_null())
            .group_by(t.season, t.event, t.round_number, t.round_name, t.club)
        )
        best_game_q = (
            Query()
            .from_(g)
            .select(g.score, g.game_date, g.season, g.event, g.week, g.round_number)
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), who_g, g.score.is_not_null())
            .order_by(g.score.desc(), g.game_date.desc())
            .limit(1)
        )
        best_game_t = (
            Query()
            .from_(t)
            .select(t.score, t.game_date, t.season, t.event, t.round_number)
            .where(~is_bye(t.player_name), who_t, t.score.is_not_null())
            .order_by(t.score.desc(), t.game_date.desc())
            .limit(1)
        )
        if season:
            league_comp = league_comp.where(g.season == season)
            tourney_comp = tourney_comp.where(t.season == season)
            league_season = league_season.where(g.season == season)
            tourney_season = tourney_season.where(t.season == season)
            league_periods = league_periods.where(g.season == season)
            tourney_periods = tourney_periods.where(t.season == season)
            best_game_q = best_game_q.where(g.season == season)
            best_game_t = best_game_t.where(t.season == season)
        league_comp_rows = fetch_dicts(con, league_comp)
        tourney_comp_rows = fetch_dicts(con, tourney_comp)
        league_season_rows = fetch_dicts(con, league_season)
        tourney_season_rows = fetch_dicts(con, tourney_season)
        league_period_rows = fetch_dicts(con, league_periods)
        tourney_period_rows = fetch_dicts(con, tourney_periods)
        best_l = fetch_dicts(con, best_game_q)
        best_t = fetch_dicts(con, best_game_t)
        ranks = _player_event_ranks(con, player_id, player_name)
        season_catalog = player_seasons(ident=ident, club=canonical)["seasons"]

    competitions = [
        _comp_row(row, is_tournament=False, player_id=player_id, player_name=player_name, ranks=ranks)
        for row in league_comp_rows
    ]
    competitions.extend(
        _comp_row(row, is_tournament=True, player_id=player_id, player_name=player_name, ranks=ranks)
        for row in tourney_comp_rows
    )
    season_totals = _merge_season_totals(league_season_rows, tourney_season_rows)
    periods = [_period_row(row, is_tournament=False, player_id=player_id, player_name=player_name) for row in league_period_rows]
    periods.extend(
        _period_row(row, is_tournament=True, player_id=player_id, player_name=player_name) for row in tourney_period_rows
    )
    seasons_payload = [*season_totals, *competitions]
    lifetime = _lifetime_from_totals(season_totals, best_l, best_t)
    highlights = player_highest_games(ident=ident, club=canonical, season=season, limit=10)["games"]
    return {
        "scope": "player",
        "player_id": player_id,
        "player_name": player_name,
        "seasons_list": season_catalog,
        "lifetime": lifetime,
        "seasons": seasons_payload,
        "periods": periods,
        "player_competitions": competitions,
        "player_season_totals": season_totals,
        "highlights": highlights,
    }


def player_aggregate(*, club: str | None = None, season: str | None = None, top_n: int | None = None) -> dict[str, Any]:
    season = (season or "").strip() or None
    if season == "all":
        season = None
    top_n = _clamp_top_n(top_n)
    period_limit = max(top_n * 6, 40)
    g = game_line
    t = tournament_line
    with session() as con:
        canonical = _canonical_club(con, club)
        league_where = [is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), g.score.is_not_null()]
        tourney_where = [~is_bye(t.player_name), t.score.is_not_null()]
        if canonical:
            league_where.append(lower(trim(g.club)) == lower(trim(canonical)))
            tourney_where.append(lower(trim(t.club)) == lower(trim(canonical)))
        if season:
            league_where.append(g.season == season)
            tourney_where.append(t.season == season)
        pk_g = coalesce(nullif(trim(g.player_id), ""), g.player_name)
        pk_t = coalesce(nullif(trim(t.player_id), ""), t.player_name)
        league_season = (
            Query()
            .from_(g)
            .select(g.season, count_star().as_("games"), sum_(g.score).as_("pins"), round_(avg_(g.score), 2).as_("average"), max_(g.score).as_("best_game"))
            .where(*league_where)
            .group_by(g.season)
        )
        tourney_season = (
            Query()
            .from_(t)
            .select(t.season, count_star().as_("games"), sum_(t.score).as_("pins"), round_(avg_(t.score), 2).as_("average"), max_(t.score).as_("best_game"))
            .where(*tourney_where)
            .group_by(t.season)
        )
        league_comp = (
            Query()
            .from_(g)
            .select(g.season, g.event, g.club, g.team, count_star().as_("games"), sum_(g.score).as_("pins"), round_(avg_(g.score), 2).as_("average"), max_(g.score).as_("best_game"), min_(g.score).as_("worst_game"))
            .where(*league_where)
            .group_by(g.season, g.event, g.club, g.team)
        )
        tourney_comp = (
            Query()
            .from_(t)
            .select(t.season, t.event, t.club, count_star().as_("games"), sum_(t.score).as_("pins"), round_(avg_(t.score), 2).as_("average"), max_(t.score).as_("best_game"), min_(t.score).as_("worst_game"))
            .where(*tourney_where)
            .group_by(t.season, t.event, t.club)
        )
        player_seasons_q = (
            Query()
            .from_(g)
            .select(
                pk_g.as_("pk"),
                any_value(g.player_name).as_("player_name"),
                any_value(g.player_id).as_("player_id"),
                g.season,
                count_star().as_("games"),
                sum_(g.score).as_("pins"),
                round_(avg_(g.score), 2).as_("average"),
                max_(g.score).as_("best_game"),
                min_(g.score).as_("worst_game"),
            )
            .where(*league_where)
            .group_by(1, 4)
        )
        player_seasons_t = (
            Query()
            .from_(t)
            .select(
                pk_t.as_("pk"),
                any_value(t.player_name).as_("player_name"),
                any_value(t.player_id).as_("player_id"),
                t.season,
                count_star().as_("games"),
                sum_(t.score).as_("pins"),
                round_(avg_(t.score), 2).as_("average"),
                max_(t.score).as_("best_game"),
                min_(t.score).as_("worst_game"),
            )
            .where(*tourney_where)
            .group_by(1, 4)
        )
        player_comp_q = (
            Query()
            .from_(g)
            .select(pk_g.as_("pk"), any_value(g.player_name).as_("player_name"), any_value(g.player_id).as_("player_id"), g.season, g.event, g.club, g.team, count_star().as_("games"), sum_(g.score).as_("pins"), round_(avg_(g.score), 2).as_("average"))
            .where(*league_where)
            .group_by(1, 4, 5, 6, 7)
        )
        player_comp_t = (
            Query()
            .from_(t)
            .select(pk_t.as_("pk"), any_value(t.player_name).as_("player_name"), any_value(t.player_id).as_("player_id"), t.season, t.event, t.club, count_star().as_("games"), sum_(t.score).as_("pins"), round_(avg_(t.score), 2).as_("average"))
            .where(*tourney_where)
            .group_by(1, 4, 5, 6)
        )
        periods_q = (
            Query()
            .from_(g)
            .select(pk_g.as_("pk"), any_value(g.player_name).as_("player_name"), any_value(g.player_id).as_("player_id"), g.season, g.event.as_("competition"), g.week, g.club, g.team, count_star().as_("games"), round_(avg_(g.score), 2).as_("average"))
            .where(*league_where, g.week.is_not_null())
            .group_by(1, 4, 5, 6, 7, 8)
            .having(count_star() >= 5)
            .order_by(round_(avg_(g.score), 2).desc())
            .limit(period_limit)
        )
        periods_t = (
            Query()
            .from_(t)
            .select(pk_t.as_("pk"), any_value(t.player_name).as_("player_name"), any_value(t.player_id).as_("player_id"), t.season, t.event.as_("competition"), t.round_number, t.round_name, t.club, count_star().as_("games"), round_(avg_(t.score), 2).as_("average"))
            .where(*tourney_where, t.round_number.is_not_null())
            .group_by(1, 4, 5, 6, 7, 8)
            .having(count_star() >= 5)
            .order_by(round_(avg_(t.score), 2).desc())
            .limit(period_limit)
        )
        best_l = fetch_dicts(con, Query().from_(g).select(g.player_name, g.score, g.game_date, g.season, g.event).where(*league_where).order_by(g.score.desc(), g.game_date.desc()).limit(1))
        best_t = fetch_dicts(con, Query().from_(t).select(t.player_name, t.score, t.game_date, t.season, t.event).where(*tourney_where).order_by(t.score.desc(), t.game_date.desc()).limit(1))
        league_season_rows = fetch_dicts(con, league_season)
        tourney_season_rows = fetch_dicts(con, tourney_season)
        league_comp_rows = fetch_dicts(con, league_comp)
        tourney_comp_rows = fetch_dicts(con, tourney_comp)
        player_season_rows = fetch_dicts(con, player_seasons_q) + fetch_dicts(con, player_seasons_t)
        player_comp_rows = fetch_dicts(con, player_comp_q)
        player_comp_t_rows = fetch_dicts(con, player_comp_t)
        period_rows = fetch_dicts(con, periods_q)
        period_t_rows = fetch_dicts(con, periods_t)
        season_catalog = player_seasons(club=canonical)["seasons"]

    season_totals = _merge_season_totals(league_season_rows, tourney_season_rows)
    competitions = [_comp_row(row, is_tournament=False) for row in league_comp_rows]
    competitions.extend(_comp_row(row, is_tournament=True) for row in tourney_comp_rows)
    player_season_totals = _merge_player_season_totals(player_season_rows)
    player_competitions = [
        _comp_row(row, is_tournament=False, player_id=as_str(row.get("player_id")), player_name=as_str(row.get("player_name")))
        for row in player_comp_rows
    ]
    player_competitions.extend(
        _comp_row(row, is_tournament=True, player_id=as_str(row.get("player_id")), player_name=as_str(row.get("player_name")))
        for row in player_comp_t_rows
    )
    periods = [
        _period_row(row, is_tournament=False, player_id=as_str(row.get("player_id")), player_name=as_str(row.get("player_name")))
        for row in period_rows
    ]
    periods.extend(
        _period_row(row, is_tournament=True, player_id=as_str(row.get("player_id")), player_name=as_str(row.get("player_name")))
        for row in period_t_rows
    )
    lifetime = _lifetime_from_totals(season_totals, best_l, best_t, player_season_totals=player_season_totals)
    trimmed = _trim_all_players(
        season_totals=season_totals,
        competitions=competitions,
        player_competitions=player_competitions,
        player_season_totals=player_season_totals,
        periods=periods,
        top_n=top_n,
    )
    return {
        "scope": "all",
        "player_id": None,
        "player_name": None,
        "top_n": top_n,
        "seasons_list": season_catalog,
        "lifetime": lifetime,
        **trimmed,
        "highlights": [],
    }


_SLIM_DROP = frozenset({"best_game", "worst_game", "vs_last_season", "rank", "competitors", "history_club"})
_MIN_GAMES_SEASON = 10
_MIN_GAMES_COMPETITION = 10
_MIN_GAMES_PERIOD = 5


def _clamp_top_n(top_n: int | None) -> int:
    if top_n is None:
        return 10
    return max(1, min(int(top_n), 50))


def _slim_stat_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in _SLIM_DROP and value is not None}


def _row_key(row: dict[str, Any]) -> tuple:
    return (
        row.get("row_type"),
        row.get("player_id") or row.get("player_name"),
        row.get("season"),
        row.get("competition"),
        row.get("club"),
        row.get("team_name"),
        row.get("period_kind"),
        row.get("period_number"),
        row.get("is_tournament"),
    )


def _union_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for rows in groups:
        for row in rows:
            key = _row_key(row)
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _top_by_average(rows: list[dict[str, Any]], n: int, *, min_games: int | None = None) -> list[dict[str, Any]]:
    eligible = [row for row in rows if row.get("average") is not None]
    if min_games:
        eligible = [row for row in eligible if (row.get("games") or 0) >= min_games]
    eligible.sort(key=lambda row: (row.get("average") or 0, row.get("games") or 0), reverse=True)
    return eligible[:n]


def _chart_competitions(competitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[bool, str], dict[str, Any]] = {}
    for row in competitions:
        name = as_str(row.get("competition")) or ""
        if not name:
            continue
        is_tournament = bool(row.get("is_tournament"))
        cur = by_key.setdefault(
            (is_tournament, name),
            {
                "competition": name,
                "is_tournament": is_tournament,
                "row_type": "competition",
                "games": 0,
                "total_pins": 0,
            },
        )
        cur["games"] += int(row.get("games") or 0)
        cur["total_pins"] += int(row.get("total_pins") or 0)
    out = []
    for cur in by_key.values():
        games = cur["games"]
        cur["average"] = round(cur["total_pins"] / games, 2) if games else None
        out.append(cur)
    return out


def _club_total_rows(player_competitions: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    by_club: dict[str, dict[str, float]] = {}
    for row in player_competitions:
        club = (as_str(row.get("club")) or "").strip()
        if not club:
            continue
        cur = by_club.setdefault(club, {"games": 0, "pins": 0.0})
        cur["games"] += int(row.get("games") or 0)
        cur["pins"] += float(row.get("total_pins") or 0)
    rollups = []
    for club, stats in by_club.items():
        games = int(stats["games"])
        rollups.append(
            {
                "row_type": "club_total",
                "club": club,
                "games": games,
                "total_pins": int(stats["pins"]),
                "average": round(stats["pins"] / games, 2) if games else None,
                "is_tournament": False,
            }
        )
    by_games = sorted(rollups, key=lambda row: (row["games"], row.get("average") or 0), reverse=True)[:n]
    by_avg = sorted(
        [row for row in rollups if row.get("average") is not None],
        key=lambda row: (row.get("average") or 0, row["games"]),
        reverse=True,
    )[:n]
    return _union_rows(by_games, by_avg)


def _affiliation_rows(player_competitions: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    by_player: dict[str, list[dict[str, Any]]] = {}
    for row in player_competitions:
        key = as_str(row.get("player_id")) or as_str(row.get("player_name")) or ""
        if not key:
            continue
        by_player.setdefault(key, []).append(row)
    ranked = sorted(
        by_player.items(),
        key=lambda item: -sum(int(row.get("games") or 0) for row in item[1]),
    )[:n]
    out: list[dict[str, Any]] = []
    for _, rows in ranked:
        out.extend(rows)
    return out


def _trim_all_players(
    *,
    season_totals: list[dict[str, Any]],
    competitions: list[dict[str, Any]],
    player_competitions: list[dict[str, Any]],
    player_season_totals: list[dict[str, Any]],
    periods: list[dict[str, Any]],
    top_n: int,
) -> dict[str, Any]:
    league_periods = [row for row in periods if not row.get("is_tournament")]
    player_competitions_out = _union_rows(
        _affiliation_rows(player_competitions, top_n),
        _top_by_average(player_competitions, top_n),
        _top_by_average(player_competitions, top_n, min_games=_MIN_GAMES_COMPETITION),
        _club_total_rows(player_competitions, top_n),
    )
    return {
        "seasons": [_slim_stat_row(row) for row in (*season_totals, *_chart_competitions(competitions))],
        "periods": [
            _slim_stat_row(row)
            for row in _union_rows(
                _top_by_average(periods, top_n),
                _top_by_average(periods, top_n, min_games=_MIN_GAMES_PERIOD),
                _top_by_average(league_periods, top_n, min_games=_MIN_GAMES_PERIOD),
            )
        ],
        "player_competitions": [_slim_stat_row(row) for row in player_competitions_out],
        "player_season_totals": [
            _slim_stat_row(row)
            for row in _union_rows(
                _top_by_average(player_season_totals, top_n),
                _top_by_average(player_season_totals, top_n, min_games=_MIN_GAMES_SEASON),
            )
        ],
    }


def _comp_row(
    row: dict[str, Any],
    *,
    is_tournament: bool,
    player_id: str | None = None,
    player_name: str | None = None,
    ranks: dict[tuple[str, str], tuple[int | None, int | None]] | None = None,
) -> dict[str, Any]:
    team = as_str(row.get("team"))
    season = as_str(row.get("season")) or ""
    event = as_str(row.get("event") or row.get("competition")) or ""
    rank_value = competitors = None
    if ranks is not None:
        rank_value, competitors = ranks.get((season, event), (None, None))
    out = {
        "season": season,
        "competition": event,
        "is_tournament": is_tournament,
        "row_type": "competition",
        "club": as_str(row.get("club")),
        "history_club": as_str(row.get("club")),
        "team_name": team,
        "team_number": _team_number(team),
        "player_name": player_name,
        "player_id": player_id or "",
        "games": as_int(row.get("games")),
        "total_pins": as_int(row.get("pins")),
        "average": as_float(row.get("average")),
        "vs_last_season": None,
        "rank": rank_value,
        "competitors": competitors,
        "best_game": {"score": as_int(row.get("best_game"))},
        "worst_game": {"score": as_int(row.get("worst_game"))},
    }
    return out


def _season_total_row(row: dict[str, Any], *, vs_last: float | None = None) -> dict[str, Any]:
    return {
        "season": as_str(row.get("season")),
        "competition": "All Events",
        "is_tournament": False,
        "row_type": "season_total",
        "club": "",
        "games": as_int(row.get("games")),
        "total_pins": as_int(row.get("pins")),
        "average": as_float(row.get("average")),
        "vs_last_season": vs_last,
        "rank": None,
        "best_game": {"score": as_int(row.get("best_game"))},
        "worst_game": {"score": as_int(row.get("worst_game"))},
    }


def _merge_player_season_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        player_id = as_str(row.get("player_id")) or ""
        player_name = as_str(row.get("player_name")) or ""
        season = as_str(row.get("season")) or ""
        key = (player_id or player_name, season)
        if not key[0] or not season:
            continue
        cur = by_key.setdefault(
            key,
            {
                "player_id": player_id,
                "player_name": player_name,
                "season": season,
                "games": 0,
                "pins": 0.0,
                "best_game": None,
                "worst_game": None,
            },
        )
        games = as_int(row.get("games")) or 0
        pins = as_float(row.get("pins")) or 0.0
        cur["games"] += games
        cur["pins"] += pins
        if player_name and not cur["player_name"]:
            cur["player_name"] = player_name
        best = as_int(row.get("best_game"))
        worst = as_int(row.get("worst_game"))
        if best is not None and (cur["best_game"] is None or best > cur["best_game"]):
            cur["best_game"] = best
        if worst is not None and (cur["worst_game"] is None or worst < cur["worst_game"]):
            cur["worst_game"] = worst
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in by_key.values():
        grouped.setdefault(str(row["player_id"] or row["player_name"]), []).append(row)
    out: list[dict[str, Any]] = []
    for plist in grouped.values():
        ordered = sorted(plist, key=lambda r: str(r["season"]))
        prev_avg = None
        for row in ordered:
            games = row["games"] or 0
            avg = round(row["pins"] / games, 2) if games else None
            vs = round(avg - prev_avg, 2) if avg is not None and prev_avg is not None else None
            if avg is not None:
                prev_avg = avg
            payload = dict(row)
            payload["average"] = avg
            item = _season_total_row(payload, vs_last=vs)
            item["player_name"] = as_str(row.get("player_name"))
            item["player_id"] = as_str(row.get("player_id")) or ""
            out.append(item)
    return out


def _merge_season_totals(league_rows: list[dict[str, Any]], tourney_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_season: dict[str, dict[str, Any]] = {}
    for row in league_rows + tourney_rows:
        season = as_str(row.get("season")) or ""
        if not season:
            continue
        cur = by_season.setdefault(season, {"season": season, "games": 0, "pins": 0.0, "best_game": None, "worst_game": None})
        games = as_int(row.get("games")) or 0
        pins = as_float(row.get("pins")) or 0.0
        cur["games"] += games
        cur["pins"] += pins
        best = as_int(row.get("best_game"))
        worst = as_int(row.get("worst_game"))
        if best is not None and (cur["best_game"] is None or best > cur["best_game"]):
            cur["best_game"] = best
        if worst is not None and (cur["worst_game"] is None or worst < cur["worst_game"]):
            cur["worst_game"] = worst
    ordered = sorted(by_season.values(), key=lambda r: str(r["season"]))
    prev_avg = None
    out = []
    for row in ordered:
        games = row["games"] or 0
        avg = round(row["pins"] / games, 2) if games else None
        vs = round(avg - prev_avg, 2) if avg is not None and prev_avg is not None else None
        if avg is not None:
            prev_avg = avg
        payload = dict(row)
        payload["average"] = avg
        out.append(_season_total_row(payload, vs_last=vs))
    return out


def _period_row(
    row: dict[str, Any],
    *,
    is_tournament: bool,
    player_id: str | None = None,
    player_name: str | None = None,
) -> dict[str, Any]:
    team = as_str(row.get("team"))
    if is_tournament:
        round_no = as_int(row.get("round_number"))
        round_name = as_str(row.get("round_name")) or (f"Round {round_no}" if round_no is not None else "")
        kind, value, number = "round", round_name, round_no
    else:
        week = as_int(row.get("week"))
        kind, value, number = "week", str(week) if week is not None else "", week
    return {
        "season": as_str(row.get("season")),
        "competition": as_str(row.get("competition") or row.get("event")),
        "is_tournament": is_tournament,
        "period_kind": kind,
        "period_value": value,
        "period_number": number,
        "player_name": player_name,
        "player_id": player_id or "",
        "games": as_int(row.get("games")),
        "average": as_float(row.get("average")),
        "club": as_str(row.get("club")),
        "team_name": team,
        "team_number": _team_number(team),
        "row_type": "period",
    }


def _lifetime_from_totals(
    season_totals: list[dict[str, Any]],
    best_l: list[dict[str, Any]],
    best_t: list[dict[str, Any]],
    player_season_totals: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not season_totals and not player_season_totals:
        return None
    games = sum(int(r.get("games") or 0) for r in season_totals)
    pins = sum(int(r.get("total_pins") or r.get("pins") or 0) for r in season_totals)
    avg = round(pins / games, 2) if games else None
    named_rows = player_season_totals or season_totals
    best_season_row = max(named_rows, key=lambda r: r.get("average") or 0, default=None)
    most = max(
        (r for r in season_totals if r.get("vs_last_season") is not None),
        key=lambda r: r.get("vs_last_season") or 0,
        default=None,
    )
    if player_season_totals:
        most = max(
            (r for r in player_season_totals if r.get("vs_last_season") is not None),
            key=lambda r: r.get("vs_last_season") or 0,
            default=None,
        )
    best_game = _pick_best_game(best_l, best_t)
    return {
        "total_games": games,
        "total_pins": int(pins) if pins is not None else 0,
        "average_score": avg,
        "best_game": best_game,
        "best_season": {
            "season": best_season_row.get("season") if best_season_row else None,
            "average": best_season_row.get("average") if best_season_row else None,
            "player_name": best_season_row.get("player_name") if best_season_row else None,
        },
        "most_improved": {
            "season": most.get("season") if most else None,
            "improvement": most.get("vs_last_season") if most else None,
            "player_name": most.get("player_name") if most else None,
        },
    }


def _pick_best_game(best_l: list[dict[str, Any]], best_t: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for row in best_l:
        candidates.append((as_int(row.get("score")) or 0, row, False))
    for row in best_t:
        candidates.append((as_int(row.get("score")) or 0, row, True))
    if not candidates:
        return {"score": None, "date": None, "event": None}
    _score, row, is_tournament = max(candidates, key=lambda item: item[0])
    event = as_str(row.get("event")) or ""
    season = as_str(row.get("season")) or ""
    name = as_str(row.get("player_name")) or ""
    label = " ".join(part for part in (name, season, event) if part).strip()
    if is_tournament and event:
        label = label or event
    return {
        "score": as_int(row.get("score")),
        "date": as_str(row.get("game_date")),
        "event": label or event,
    }


def _player_event_ranks(con, player_id: str | None, player_name: str | None) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    g = game_line
    t = tournament_line
    pk_g = coalesce(nullif(trim(g.player_id), ""), g.player_name)
    pk_t = coalesce(nullif(trim(t.player_id), ""), t.player_name)
    player_key = player_id or player_name or ""
    league_played = (
        Query()
        .from_(g)
        .select(g.season, g.event, pk_g.as_("pk"), avg_(g.score).as_("value"))
        .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name))
        .group_by(1, 2, 3)
    )
    lp = relation("played", "season", "event", "pk", "value")
    league_ranked = (
        Query()
        .from_(lp)
        .select(
            lp.season,
            lp.event,
            lp.pk,
            rank().over(partition_by=[lp.season, lp.event], order_by=[lp.value.desc()]).as_("rk"),
            count_star().over(partition_by=[lp.season, lp.event]).as_("competitors"),
        )
    )
    lr = relation("ranked", "season", "event", "pk", "rk", "competitors")
    tourney_played = (
        Query()
        .from_(t)
        .select(
            t.season,
            t.event,
            pk_t.as_("pk"),
            coalesce(max_(t.overall_cumulative_score), sum_(t.score)).as_("value"),
        )
        .where(~is_bye(t.player_name))
        .group_by(1, 2, 3)
    )
    tp = relation("tplayed", "season", "event", "pk", "value")
    tourney_ranked = (
        Query()
        .from_(tp)
        .select(
            tp.season,
            tp.event,
            tp.pk,
            rank().over(partition_by=[tp.season, tp.event], order_by=[tp.value.desc()]).as_("rk"),
            count_star().over(partition_by=[tp.season, tp.event]).as_("competitors"),
        )
    )
    tr = relation("tranked", "season", "event", "pk", "rk", "competitors")
    league_rows = fetch_dicts(
        con,
        Query.with_ctes(played=league_played, ranked=league_ranked)
        .from_(lr)
        .select(lr.season, lr.event, lr.rk, lr.competitors)
        .where(lr.pk == player_key),
    )
    tourney_rows = fetch_dicts(
        con,
        Query.with_ctes(tplayed=tourney_played, tranked=tourney_ranked)
        .from_(tr)
        .select(tr.season, tr.event, tr.rk, tr.competitors)
        .where(tr.pk == player_key),
    )
    out: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for row in league_rows + tourney_rows:
        out[(as_str(row.get("season")) or "", as_str(row.get("event")) or "")] = (
            as_int(row.get("rk")),
            as_int(row.get("competitors")),
        )
    return out


def player_tournaments(
    ident: str,
    *,
    season: str | None = None,
    event: str | None = None,
) -> dict[str, Any]:
    t = tournament_line
    group_filter = normalize_tournament_group_name(event) if event else ""
    with session() as con:
        player_id, player_name = _resolve_player(con, ident)
        if not player_name and not player_id:
            return {"player_id": None, "player_name": ident, "results": []}
        who = _who(t, player_id, player_name)
        q = (
            Query()
            .from_(t)
            .select(t.season, t.event, t.club, round_(avg_(t.score), 2).as_("average"))
            .where(~is_bye(t.player_name), who)
            .group_by(t.season, t.event, t.club)
            .order_by(t.season.desc(), t.event)
        )
        if season:
            q = q.where(t.season == season)
        pairs = fetch_dicts(con, q)
    results = []
    for row in pairs:
        event_name = as_str(row["event"]) or ""
        group = normalize_tournament_group_name(event_name) or event_name
        if group_filter and group != group_filter and event_name != event:
            continue
        section = tournament_section(str(row["season"]), event_name)
        lb = next(
            (
                r
                for r in section["leaderboard"]
                if (player_id and r.get("player_id") == player_id)
                or (player_name and str(r.get("player_name") or r.get("player") or "") == player_name)
            ),
            None,
        )
        results.append(
            {
                "season": as_str(row["season"]),
                "tournament": group or event_name,
                "tournament_group": group or event_name,
                "position": lb["rank"] if lb else None,
                "average": as_float(row["average"]),
                "club": as_str(row["club"]),
            }
        )
    return {"player_id": player_id, "player_name": player_name, "results": results}
