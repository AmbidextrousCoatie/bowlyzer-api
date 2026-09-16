"""Club history: teams × seasons with league and table position."""

from __future__ import annotations

import re
from typing import Any

from bowlyzerapi.engine import (
    Query,
    abs_,
    avg_,
    case,
    count_distinct,
    count_star,
    fetch_dicts,
    fetch_rows,
    game_line,
    lower,
    max_,
    rank,
    regexp_extract,
    relation,
    round_,
    sum_,
    tournament_line,
    trim,
    try_cast,
)
from bowlyzerapi.queries.filters import (
    is_bye,
    is_league_fact,
    is_player_game,
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


def club_rankings(*, top_n: int = 5) -> dict[str, Any]:
    top_n = max(1, min(int(top_n), 20))
    g = game_line
    t = tournament_line
    with session() as con:
        pinfall = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.club, sum_(abs_(g.score)).as_("value"))
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), g.club.is_not_null())
            .group_by(g.club)
            .order_by(sum_(abs_(g.score)).desc())
            .limit(top_n),
        )
        members = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.club, count_distinct(g.player_id).as_("value"))
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), g.club.is_not_null())
            .group_by(g.club)
            .order_by(count_distinct(g.player_id).desc())
            .limit(top_n),
        )
        weekly_avg = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.club, g.season, g.event, g.week, g.team, avg_(abs_(g.score)).as_("avg"))
            .where(is_league_fact(g), is_player_game(g), ~is_bye(g.player_name), g.club.is_not_null())
            .group_by(g.club, g.season, g.event, g.week, g.team)
            .order_by(avg_(abs_(g.score)).desc())
            .limit(top_n),
        )
        team_game = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.club, g.team, g.season, g.event, g.week, max_(g.score).as_("value"))
            .where(g.computed_data.is_true(), g.club.is_not_null(), g.score.is_not_null())
            .group_by(g.club, g.team, g.season, g.event, g.week)
            .order_by(max_(g.score).desc())
            .limit(top_n),
        )
    with session() as con:
        totals = fetch_dicts(
            con,
            Query()
            .from_(t)
            .select(t.season, t.event, t.club, t.player_name, sum_(t.score).as_("pins"))
            .where(~is_bye(t.player_name), t.club.is_not_null())
            .group_by(t.season, t.event, t.club, t.player_name),
        )
    winners: dict[tuple[str, str], str] = {}
    best: dict[tuple[str, str], float] = {}
    for row in totals:
        key = (str(row["season"]), str(row["event"]))
        pins = float(row["pins"] or 0)
        if key not in best or pins > best[key]:
            best[key] = pins
            winners[key] = str(row["club"])
    from collections import Counter

    win_counts = Counter(winners.values())
    tournament_win_rows = [{"club": club, "value": n} for club, n in win_counts.most_common(top_n)]

    return {
        "top_n": top_n,
        "highest_total_pinfall": [{"club": as_str(r["club"]), "value": as_float(r["value"])} for r in pinfall],
        "most_members": [{"club": as_str(r["club"]), "value": as_int(r["value"])} for r in members],
        "highest_weekly_team_average": [
            {
                "club": as_str(r["club"]),
                "team": as_str(r["team"]),
                "season": as_str(r["season"]),
                "league": as_str(r["event"]),
                "week": as_int(r["week"]),
                "value": as_float(r["avg"]),
            }
            for r in weekly_avg
        ],
        "highest_team_game_average": [
            {
                "club": as_str(r["club"]),
                "team": as_str(r["team"]),
                "season": as_str(r["season"]),
                "league": as_str(r["event"]),
                "week": as_int(r["week"]),
                "value": as_float(r["value"]),
            }
            for r in team_game
        ],
        "most_tournament_wins": tournament_win_rows,
        "most_league_wins": _league_wins(top_n),
    }


def _league_wins(top_n: int) -> list[dict[str, Any]]:
    g = game_line
    with session() as con:
        rows = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.season, g.event, g.club, g.team, sum_(g.points).as_("pts"))
            .where(is_league_fact(g), g.club.is_not_null(), g.team.is_not_null())
            .group_by(g.season, g.event, g.club, g.team),
        )
    best: dict[tuple[str, str], tuple[float, str]] = {}
    for row in rows:
        key = (str(row["season"]), str(row["event"]))
        pts = float(row["pts"] or 0)
        club = str(row["club"])
        if key not in best or pts > best[key][0]:
            best[key] = (pts, club)
    from collections import Counter

    counts = Counter(club for _pts, club in best.values())
    return [{"club": club, "value": n} for club, n in counts.most_common(top_n)]


def _empty_legends() -> dict[str, list]:
    return {
        "most_seasons": [],
        "most_games": [],
        "highest_average": [],
        "best_seasons": [],
        "most_teams_represented": [],
        "most_leagues_seen": [],
    }


def _legends(con, club: str, season: str | None) -> dict[str, Any]:
    g = game_line
    scope = (
        is_league_fact(g)
        & is_player_game(g)
        & (~is_bye(g.player_name))
        & (lower(trim(g.club)) == lower(trim(club)))
    )
    if season:
        scope = scope & (g.season == season)
    most_games = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), count_star().as_("value"))
        .where(scope)
        .group_by(g.player_name)
        .order_by(count_star().desc())
        .limit(5),
    )
    most_seasons = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), count_distinct(g.season).as_("value"))
        .where(scope)
        .group_by(g.player_name)
        .order_by(count_distinct(g.season).desc())
        .limit(5),
    )
    highest_average = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), round_(avg_(abs_(g.score)), 2).as_("value"), count_star().as_("games"))
        .where(scope)
        .group_by(g.player_name)
        .having(count_star() >= (6 if season else 12))
        .order_by(avg_(abs_(g.score)).desc())
        .limit(5),
    )
    best_seasons = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.player_name.as_("player"),
            g.season,
            round_(avg_(abs_(g.score)), 2).as_("value"),
            count_star().as_("games"),
        )
        .where(scope)
        .group_by(g.player_name, g.season)
        .having(count_star() >= 6)
        .order_by(avg_(abs_(g.score)).desc())
        .limit(5),
    )
    most_teams = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), count_distinct(g.team).as_("value"))
        .where(scope)
        .group_by(g.player_name)
        .order_by(count_distinct(g.team).desc())
        .limit(5),
    )
    most_leagues = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), count_distinct(g.event).as_("value"))
        .where(scope)
        .group_by(g.player_name)
        .order_by(count_distinct(g.event).desc())
        .limit(5),
    )

    def pack(rows, extra=None):
        out = []
        for r in rows:
            item = {"player": as_str(r["player"]), "value": r.get("value")}
            if extra:
                for key in extra:
                    item[key] = r.get(key)
            out.append(item)
        return out

    return {
        "most_seasons": pack(most_seasons),
        "most_games": pack(most_games),
        "highest_average": pack(highest_average, ["games"]),
        "best_seasons": pack(best_seasons, ["season", "games"]),
        "most_teams_represented": pack(most_teams),
        "most_leagues_seen": pack(most_leagues),
    }
