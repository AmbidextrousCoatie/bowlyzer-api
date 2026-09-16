"""Club history: teams × seasons with league and table position."""

from __future__ import annotations

import re
from typing import Any

from bowlyzerapi.engine import (
    Query,
    abs_,
    any_value,
    avg_,
    case,
    coalesce,
    count_distinct,
    count_star,
    fetch_dicts,
    fetch_rows,
    game_line,
    list_distinct,
    lower,
    max_,
    nullif,
    rank,
    regexp_extract,
    relation,
    round_,
    row_number,
    sum_,
    tournament_line,
    trim,
    try_cast,
)
from bowlyzerapi.queries.filters import (
    is_bye,
    is_league_fact,
    is_player_game,
    pins,
    resolve_club,
)
from bowlyzerapi.queries.util import as_float, as_int, as_str
from bowlyzerapi.warehouse import connect, session


def club_history(club: str) -> dict[str, Any]:
    club = (club or "").strip()
    if not club:
        return {"club": "", "seasons": [], "rows": []}

    con = connect()
    try:
        canonical = resolve_club(con, club)
        if canonical is None:
            return {"club": club, "seasons": [], "rows": []}
        cells = fetch_rows(con, _club_matrix(canonical))
    finally:
        con.close()

    seasons = sorted({str(row[1]) for row in cells})
    by_team: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for team_number, season, event, team, pos, team_count in cells:
        key = str(team_number or "base")
        by_team.setdefault(key, {})
        by_team[key].setdefault(str(season), [])
        by_team[key][str(season)].append(
            {
                "league": event,
                "team": team,
                "final_position": int(pos) if pos is not None else None,
                "team_count": int(team_count) if team_count is not None else None,
            }
        )

    def _team_sort(label: str) -> tuple[int, Any]:
        return (0, int(label)) if str(label).isdigit() else (1, str(label))

    rows = []
    for team_number in sorted(by_team, key=_team_sort):
        season_cells = {season: {"items": by_team[team_number].get(season, [])} for season in seasons}
        rows.append({"team_number": team_number, "seasons": season_cells})

    return {"club": canonical, "seasons": seasons, "rows": rows}


def _club_matrix(canonical: str) -> Query:
    g = game_line
    club_games = relation("club_games", "season", "event", "team")
    numbered = relation("numbered", "season", "event", "team", "team_number")
    appearances = relation("appearances", "season", "event", "team", "team_number").as_("a")
    standings = relation("standings", "season", "event", "team", "pts")
    ranked = relation("ranked", "season", "event", "team", "final_position").as_("r")
    team_counts = relation("team_counts", "season", "event", "team_count").as_("t")

    return (
        Query.with_ctes(
            club_games=(
                Query().from_(g)
                .select(g.season, g.event, g.team)
                .where(
                    g.computed_data.is_true(),
                    lower(trim(g.club)) == lower(trim(canonical)),
                    g.team.is_not_null(),
                )
            ),
            numbered=(
                Query().from_(club_games)
                .select(
                    club_games.season,
                    club_games.event,
                    club_games.team,
                    regexp_extract(club_games.team, " ([0-9]+)$", 1).as_("team_number"),
                )
            ),
            appearances=(
                Query().from_(numbered)
                .select(
                    numbered.season,
                    numbered.event,
                    numbered.team,
                    case((numbered.team_number == "", "base"), else_=numbered.team_number).as_(
                        "team_number"
                    ),
                )
                .group_by(1, 2, 3, 4)
            ),
            standings=(
                Query().from_(g)
                .select(g.season, g.event, g.team, sum_(g.points).as_("pts"))
                .group_by(g.season, g.event, g.team)
            ),
            ranked=(
                Query().from_(standings)
                .select(
                    standings.season,
                    standings.event,
                    standings.team,
                    rank()
                    .over(
                        partition_by=[standings.season, standings.event],
                        order_by=[standings.pts.desc()],
                    )
                    .as_("final_position"),
                )
            ),
            team_counts=(
                Query().from_(g)
                .select(g.season, g.event, count_distinct(g.team).as_("team_count"))
                .group_by(1, 2)
            ),
        )
        .from_(appearances)
        .left_join(
            ranked,
            on=(
                (ranked.season == appearances.season)
                & (ranked.event == appearances.event)
                & (ranked.team == appearances.team)
            ),
        )
        .left_join(
            team_counts,
            on=(team_counts.season == appearances.season) & (team_counts.event == appearances.event),
        )
        .select(
            appearances.team_number,
            appearances.season,
            appearances.event,
            appearances.team,
            ranked.final_position,
            team_counts.team_count,
        )
        .order_by(
            try_cast(appearances.team_number, "INTEGER").nulls_last(),
            appearances.team_number,
            appearances.season,
            appearances.event,
        )
    )


_TEAM_NUMBER = re.compile(r" (\d+)$")


def club_list(*, unnumbered: bool = False) -> dict[str, Any]:
    g = game_line
    with session() as con:
        rows = fetch_rows(
            con,
            Query()
            .from_(g)
            .select(g.club, g.team)
            .where(g.club.is_not_null(), trim(g.club) != "", g.team.is_not_null())
            .distinct(),
        )
    clubs: set[str] = set()
    with_unnumbered: set[str] = set()
    with_numbered: set[str] = set()
    for club, team in rows:
        name = str(club).strip()
        if not name:
            continue
        clubs.add(name)
        if _TEAM_NUMBER.search(str(team).strip()):
            with_numbered.add(name)
        else:
            with_unnumbered.add(name)
    if unnumbered:
        return {"clubs": sorted(with_unnumbered & with_numbered)}
    return {"clubs": sorted(clubs)}


def club_legends(club: str, *, season: str | None = None) -> dict[str, Any]:
    club = (club or "").strip()
    if not club:
        return {"club": "", **_empty_legends()}
    with session() as con:
        canonical = resolve_club(con, club)
        if canonical is None:
            return {"club": club, **_empty_legends()}
        legends = _legends(con, canonical, season)
    return {"club": canonical, **legends}


def club_document(club: str, *, season: str | None = None) -> dict[str, Any]:
    with session() as con:
        canonical = resolve_club(con, club)
        if canonical is None:
            return {"club": club, "legends": _empty_legends()}
        legends = _legends(con, canonical, season)
    history = club_history(canonical)
    return {"club": canonical, "legends": legends, "history": history}


def club_players(club: str, *, season: str | None = None) -> dict[str, Any]:
    g = game_line
    with session() as con:
        canonical = resolve_club(con, club)
        if canonical is None:
            return {"club": club, "players": []}
        q = (
            Query()
            .from_(g)
            .select(
                g.player_name.as_("player"),
                g.player_id,
                count_star().as_("games"),
                round_(avg_(abs_(g.score)), 2).as_("average"),
                max_(abs_(g.score)).as_("high_game"),
                count_distinct(g.season).as_("seasons"),
            )
            .where(
                is_league_fact(g),
                is_player_game(g),
                ~is_bye(g.player_name),
                lower(trim(g.club)) == lower(trim(canonical)),
            )
            .group_by(g.player_name, g.player_id)
            .order_by(avg_(abs_(g.score)).desc())
        )
        if season:
            q = q.where(g.season == season)
        rows = fetch_dicts(con, q)
    return {
        "club": canonical,
        "season": season,
        "players": [
            {
                "player": as_str(r["player"]),
                "player_id": as_str(r["player_id"]),
                "games": as_int(r["games"]),
                "average": as_float(r["average"]),
                "high_game": as_int(r["high_game"]),
                "seasons": as_int(r["seasons"]),
            }
            for r in rows
        ],
    }


def club_honor_300(club: str | None = None) -> dict[str, Any]:
    g = game_line
    t = tournament_line
    with session() as con:
        canonical = resolve_club(con, club) if club else None
        league_q = (
            Query()
            .from_(g)
            .select(
                g.player_name.as_("player"),
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
            .where(is_player_game(g), ~is_bye(g.player_name), g.score == 300)
        )
        tourney_q = (
            Query()
            .from_(t)
            .select(
                t.player_name.as_("player"),
                t.player_id,
                t.score,
                t.game_date,
                t.season,
                t.event.as_("competition"),
                t.club,
                t.club.as_("team"),
                t.round_number.as_("week"),
                t.round_number,
            )
            .where(~is_bye(t.player_name), t.score == 300)
        )
        if canonical:
            league_q = league_q.where(lower(trim(g.club)) == lower(trim(canonical)))
            tourney_q = tourney_q.where(lower(trim(t.club)) == lower(trim(canonical)))
        rows = fetch_dicts(con, league_q) + fetch_dicts(con, tourney_q)
    rows.sort(key=lambda r: str(r.get("game_date") or ""), reverse=True)
    return {
        "club": canonical,
        "games": [
            {
                "player": as_str(r["player"]),
                "player_id": as_str(r["player_id"]),
                "score": as_int(r["score"]),
                "date": as_str(r["game_date"]),
                "season": as_str(r["season"]),
                "competition": as_str(r["competition"]),
                "club": as_str(r["club"]),
                "team": as_str(r["team"]),
                "week": as_int(r.get("week")),
                "round": as_int(r.get("round_number")),
            }
            for r in rows
        ],
    }


def _count_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        club = as_str(row.get("club"))
        if not club:
            continue
        out.append({"club": club, "value": as_int(row.get("value")) or 0})
    return out


def _detail_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        club = as_str(row.get("club"))
        value = as_float(row.get("value"))
        if not club or value is None:
            continue
        entry: dict[str, Any] = {
            "club": club,
            "value": value,
            "team": as_str(row.get("team")) or "",
            "season": as_str(row.get("season")) or "",
            "league": as_str(row.get("league") or row.get("event")) or "",
            "week": as_str(row.get("week")) or "",
        }
        round_no = as_int(row.get("round") if row.get("round") is not None else row.get("round_number"))
        if round_no is not None:
            entry["round"] = str(round_no)
        match_total = as_int(row.get("match_total"))
        if match_total is not None:
            entry["match_total"] = match_total
        out.append(entry)
    return out


def _player_league_where(g=game_line):
    player_key = coalesce(nullif(trim(g.player_id), ""), g.player_name)
    return (
        is_league_fact(g),
        is_player_game(g),
        ~is_bye(g.player_name),
        g.club.is_not_null(),
        trim(g.club) != "",
        player_key.is_not_null(),
        trim(player_key) != "",
        g.score.is_not_null(),
    )


def club_rankings(*, top_n: int = 5) -> dict[str, Any]:
    """Warehouse-wide club leaderboards for the empty `/club` page."""
    top_n = max(1, min(int(top_n), 20))
    g = game_line
    player_key = coalesce(nullif(trim(g.player_id), ""), g.player_name)
    league_where = _player_league_where(g)
    with session() as con:
        pinfall = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.club, sum_(g.score).as_("value"))
            .where(*league_where)
            .group_by(g.club)
            .order_by(sum_(g.score).desc())
            .limit(top_n),
        )
        members = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.club, count_distinct(player_key).as_("value"))
            .where(*league_where)
            .group_by(g.club)
            .order_by(count_distinct(player_key).desc())
            .limit(top_n),
        )
        weekly = _best_weekly_averages(con, top_n)
        team_games = _best_team_games(con, top_n)
        tournament_wins = _tournament_wins(con, top_n)
        league_wins = _league_wins(con, top_n)
    return {
        "top_n": top_n,
        "highest_total_pinfall": _count_entries(pinfall),
        "most_members": _count_entries(members),
        "highest_weekly_team_average": weekly,
        "highest_team_game_average": team_games,
        "most_tournament_wins": tournament_wins,
        "most_league_wins": league_wins,
    }


def _match_averages() -> Query:
    g = game_line
    round_no = try_cast(g.round_number, "INTEGER")
    return (
        Query()
        .from_(g)
        .select(
            g.club,
            g.team,
            g.season,
            g.event,
            g.week,
            round_no.as_("round_number"),
            avg_(g.score).as_("match_average"),
            sum_(g.score).as_("match_total"),
        )
        .where(
            *_player_league_where(g),
            g.team.is_not_null(),
            round_no.is_not_null(),
            round_no > 0,
        )
        .group_by(g.club, g.team, g.season, g.event, g.week, round_no)
    )


def _best_weekly_averages(con, top_n: int) -> list[dict[str, Any]]:
    matches = _match_averages()
    m = relation(
        "matches",
        "club",
        "team",
        "season",
        "event",
        "week",
        "round_number",
        "match_average",
        "match_total",
    )
    weekly = (
        Query()
        .from_(m)
        .select(
            m.club,
            m.team,
            m.season,
            m.event,
            m.week,
            avg_(m.match_average).as_("week_average"),
        )
        .group_by(m.club, m.team, m.season, m.event, m.week)
    )
    w = relation("weekly", "club", "team", "season", "event", "week", "week_average")
    ranked = (
        Query()
        .from_(w)
        .select(
            w.club,
            w.team,
            w.season,
            w.event,
            w.week,
            w.week_average,
            row_number()
            .over(partition_by=[w.club], order_by=[w.week_average.desc()])
            .as_("rn"),
        )
    )
    r = relation("ranked", "club", "team", "season", "event", "week", "week_average", "rn")
    rows = fetch_dicts(
        con,
        Query.with_ctes(matches=matches, weekly=weekly, ranked=ranked)
        .from_(r)
        .select(
            r.club,
            r.team,
            r.season,
            r.event.as_("league"),
            r.week,
            r.week_average.as_("value"),
        )
        .where(r.rn == 1)
        .order_by(r.week_average.desc())
        .limit(top_n),
    )
    return _detail_entries(rows)


def _best_team_games(con, top_n: int) -> list[dict[str, Any]]:
    matches = _match_averages()
    m = relation(
        "matches",
        "club",
        "team",
        "season",
        "event",
        "week",
        "round_number",
        "match_average",
        "match_total",
    )
    ranked = (
        Query()
        .from_(m)
        .select(
            m.club,
            m.team,
            m.season,
            m.event,
            m.week,
            m.round_number,
            m.match_average,
            m.match_total,
            row_number()
            .over(partition_by=[m.club], order_by=[m.match_average.desc()])
            .as_("rn"),
        )
    )
    r = relation(
        "ranked",
        "club",
        "team",
        "season",
        "event",
        "week",
        "round_number",
        "match_average",
        "match_total",
        "rn",
    )
    rows = fetch_dicts(
        con,
        Query.with_ctes(matches=matches, ranked=ranked)
        .from_(r)
        .select(
            r.club,
            r.team,
            r.season,
            r.event.as_("league"),
            r.week,
            r.round_number.as_("round"),
            r.match_average.as_("value"),
            r.match_total,
        )
        .where(r.rn == 1)
        .order_by(r.match_average.desc())
        .limit(top_n),
    )
    return _detail_entries(rows)


def _league_wins(con, top_n: int) -> list[dict[str, Any]]:
    g = game_line
    totals = (
        Query()
        .from_(g)
        .select(
            g.season,
            g.event,
            g.team,
            any_value(g.club).as_("club"),
            sum_(g.points).as_("pts"),
        )
        .where(is_league_fact(g), g.team.is_not_null(), g.club.is_not_null(), trim(g.club) != "")
        .group_by(g.season, g.event, g.team)
    )
    tot = relation("totals", "season", "event", "team", "club", "pts")
    ranked = (
        Query()
        .from_(tot)
        .select(
            tot.club,
            rank()
            .over(partition_by=[tot.season, tot.event], order_by=[tot.pts.desc()])
            .as_("rk"),
        )
    )
    rk = relation("ranked", "club", "rk")
    rows = fetch_dicts(
        con,
        Query.with_ctes(totals=totals, ranked=ranked)
        .from_(rk)
        .select(rk.club, count_star().as_("value"))
        .where(rk.rk == 1, rk.club.is_not_null(), trim(rk.club) != "")
        .group_by(rk.club)
        .order_by(count_star().desc())
        .limit(top_n),
    )
    return _count_entries(rows)


def _tournament_wins(con, top_n: int) -> list[dict[str, Any]]:
    t = tournament_line
    played = (
        Query()
        .from_(t)
        .select(
            t.season,
            t.event,
            t.player_name,
            any_value(t.club).as_("club"),
            sum_(pins(t, True)).as_("total_pins"),
        )
        .where(~is_bye(t.player_name), t.club.is_not_null(), trim(t.club) != "")
        .group_by(t.season, t.event, t.player_name)
    )
    p = relation("played", "season", "event", "player_name", "club", "total_pins")
    ranked = (
        Query()
        .from_(p)
        .select(
            p.club,
            row_number()
            .over(partition_by=[p.season, p.event], order_by=[p.total_pins.desc()])
            .as_("rn"),
        )
    )
    r = relation("ranked", "club", "rn")
    rows = fetch_dicts(
        con,
        Query.with_ctes(played=played, ranked=ranked)
        .from_(r)
        .select(r.club, count_star().as_("value"))
        .where(r.rn == 1, r.club.is_not_null(), trim(r.club) != "")
        .group_by(r.club)
        .order_by(count_star().desc())
        .limit(top_n),
    )
    return _count_entries(rows)


_LEGEND_TOP_N = 5
_MIN_GAMES_ALLTIME_AVG = 12
_MIN_GAMES_SEASON = 6
_MIN_TEAMS_REPRESENTED = 2
_MIN_LEAGUES_SEEN = 2


def _empty_legends() -> dict[str, list]:
    return {
        "most_seasons": [],
        "most_games": [],
        "highest_average": [],
        "best_seasons": [],
        "most_teams_represented": [],
        "most_leagues_seen": [],
    }


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _legend_entry(row: dict[str, Any], *, extras: tuple[str, ...] = ()) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "player_id": as_str(row.get("player_id")) or "",
        "player_name": as_str(row.get("player_name")) or as_str(row.get("player")) or "",
        "value": row.get("value"),
    }
    if "games" in extras:
        games = as_int(row.get("games"))
        if games is not None:
            entry["games"] = games
    if "average" in extras:
        avg = as_float(row.get("average"))
        if avg is not None:
            entry["average"] = avg
            entry["value"] = avg
    if "season" in extras:
        season = as_str(row.get("season"))
        if season:
            entry["season"] = season
    if "teams" in extras:
        teams = _str_list(row.get("teams"))
        if teams:
            entry["teams"] = teams
    if "leagues" in extras:
        leagues = _str_list(row.get("leagues"))
        if leagues:
            entry["leagues"] = leagues
    return entry


def _legends(con, club: str, season: str | None) -> dict[str, Any]:
    g = game_line
    player_key = coalesce(nullif(trim(g.player_id), ""), g.player_name)
    team_number = regexp_extract(g.team, " ([0-9]+)$", 1)
    team_slot = case((team_number == "", "Basis"), else_=team_number)
    scope = (
        is_league_fact(g)
        & is_player_game(g)
        & (~is_bye(g.player_name))
        & (lower(trim(g.club)) == lower(trim(club)))
        & g.player_name.is_not_null()
        & (trim(g.player_name) != "")
    )
    if season:
        scope = scope & (g.season == season)
    min_avg_games = _MIN_GAMES_SEASON if season else _MIN_GAMES_ALLTIME_AVG

    def top(query: Query) -> list[dict[str, Any]]:
        return fetch_dicts(con, query.limit(_LEGEND_TOP_N))

    most_games = top(
        Query()
        .from_(g)
        .select(
            any_value(g.player_id).as_("player_id"),
            any_value(g.player_name).as_("player_name"),
            count_star().as_("value"),
            count_star().as_("games"),
            round_(avg_(abs_(g.score)), 2).as_("average"),
        )
        .where(scope)
        .group_by(player_key)
        .order_by(count_star().desc(), any_value(g.player_name).asc())
    )
    highest_average = top(
        Query()
        .from_(g)
        .select(
            any_value(g.player_id).as_("player_id"),
            any_value(g.player_name).as_("player_name"),
            round_(avg_(abs_(g.score)), 2).as_("value"),
            round_(avg_(abs_(g.score)), 2).as_("average"),
            count_star().as_("games"),
        )
        .where(scope)
        .group_by(player_key)
        .having(count_star() >= min_avg_games)
        .order_by(avg_(abs_(g.score)).desc(), count_star().desc())
    )
    most_teams = top(
        Query()
        .from_(g)
        .select(
            any_value(g.player_id).as_("player_id"),
            any_value(g.player_name).as_("player_name"),
            count_distinct(team_slot).as_("value"),
            list_distinct(team_slot).as_("teams"),
        )
        .where(scope, g.team.is_not_null())
        .group_by(player_key)
        .having(count_distinct(team_slot) >= _MIN_TEAMS_REPRESENTED)
        .order_by(count_distinct(team_slot).desc(), any_value(g.player_name).asc())
    )
    most_leagues = top(
        Query()
        .from_(g)
        .select(
            any_value(g.player_id).as_("player_id"),
            any_value(g.player_name).as_("player_name"),
            count_distinct(g.event).as_("value"),
            list_distinct(g.event).as_("leagues"),
        )
        .where(scope, g.event.is_not_null())
        .group_by(player_key)
        .having(count_distinct(g.event) >= _MIN_LEAGUES_SEEN)
        .order_by(count_distinct(g.event).desc(), any_value(g.player_name).asc())
    )

    most_seasons: list[dict[str, Any]] = []
    best_seasons: list[dict[str, Any]] = []
    if not season:
        most_seasons = top(
            Query()
            .from_(g)
            .select(
                any_value(g.player_id).as_("player_id"),
                any_value(g.player_name).as_("player_name"),
                count_distinct(g.season).as_("value"),
            )
            .where(scope)
            .group_by(player_key)
            .order_by(count_distinct(g.season).desc(), any_value(g.player_name).asc())
        )
        best_seasons = top(
            Query()
            .from_(g)
            .select(
                any_value(g.player_id).as_("player_id"),
                any_value(g.player_name).as_("player_name"),
                g.season,
                round_(avg_(abs_(g.score)), 2).as_("value"),
                round_(avg_(abs_(g.score)), 2).as_("average"),
                count_star().as_("games"),
            )
            .where(scope)
            .group_by(player_key, g.season)
            .having(count_star() >= _MIN_GAMES_SEASON)
            .order_by(avg_(abs_(g.score)).desc(), count_star().desc())
        )

    return {
        "most_seasons": [_legend_entry(row) for row in most_seasons],
        "most_games": [_legend_entry(row, extras=("games", "average")) for row in most_games],
        "highest_average": [_legend_entry(row, extras=("games", "average")) for row in highest_average],
        "best_seasons": [_legend_entry(row, extras=("season", "games", "average")) for row in best_seasons],
        "most_teams_represented": [_legend_entry(row, extras=("teams",)) for row in most_teams],
        "most_leagues_seen": [_legend_entry(row, extras=("leagues",)) for row in most_leagues],
    }
