"""League standings, series, honor, timetable, matchday, compare, records."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bowlyzerapi.engine import (
    Query,
    abs_,
    avg_,
    coalesce,
    count_star,
    fetch_dicts,
    fetch_scalar,
    game_line,
    max_,
    min_,
    round_,
    sum_,
)
from bowlyzerapi.queries.filters import (
    event_scope,
    is_bye,
    is_player_game,
    is_team_total,
)
from bowlyzerapi.queries.util import as_float, as_int, as_str
from bowlyzerapi.warehouse import session


def _latest_week(con, season: str, league: str) -> int | None:
    g = game_line
    return as_int(
        fetch_scalar(
            con,
            Query()
            .from_(g)
            .select(max_(g.week))
            .where(event_scope(g, season, league), g.week.is_not_null()),
        )
    )


def _weekly_rows(con, season: str, league: str, through_week: int) -> list[dict[str, Any]]:
    g = game_line
    scope = event_scope(g, season, league) & (g.week <= through_week) & g.week.is_not_null()
    players = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.team,
            g.week,
            sum_(abs_(g.score)).as_("pins"),
            sum_(g.points).as_("player_pts"),
            count_star().as_("games"),
            avg_(abs_(g.score)).as_("average"),
        )
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.team.is_not_null())
        .group_by(g.team, g.week),
    )
    teams = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.team,
            g.week,
            (sum_(g.points) + coalesce(sum_(g.bonus_points), 0)).as_("match_pts"),
        )
        .where(scope, is_team_total(g), g.team.is_not_null())
        .group_by(g.team, g.week),
    )
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for row in players:
        key = (str(row["team"]), int(row["week"]))
        merged[key] = {
            "team": key[0],
            "week": key[1],
            "pins": as_float(row["pins"]) or 0.0,
            "player_pts": as_float(row["player_pts"]) or 0.0,
            "match_pts": 0.0,
            "games": as_int(row["games"]) or 0,
            "average": as_float(row["average"]),
        }
    for row in teams:
        key = (str(row["team"]), int(row["week"]))
        slot = merged.setdefault(
            key,
            {
                "team": key[0],
                "week": key[1],
                "pins": 0.0,
                "player_pts": 0.0,
                "match_pts": 0.0,
                "games": 0,
                "average": None,
            },
        )
        slot["match_pts"] = as_float(row["match_pts"]) or 0.0
    for slot in merged.values():
        slot["points"] = float(slot["player_pts"]) + float(slot["match_pts"])
    return list(merged.values())


def _standings_from_weeks(weekly: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    history: dict[str, dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in weekly:
        team = row["team"]
        acc = totals.setdefault(
            team,
            {"team": team, "points": 0.0, "pins": 0.0, "games": 0},
        )
        acc["points"] += row["points"]
        acc["pins"] += row["pins"]
        acc["games"] += row["games"]
        history[team][row["week"]] = {
            "points": round(row["points"], 2),
            "pins": round(row["pins"], 1),
            "average": round(row["average"], 2) if row["average"] is not None else None,
            "games": row["games"],
        }
    ranked = sorted(totals.values(), key=lambda r: (-r["points"], -r["pins"], r["team"]))
    out = []
    for i, row in enumerate(ranked, start=1):
        games = row["games"] or 0
        out.append(
            {
                "rank": i,
                "team": row["team"],
                "points": round(row["points"], 2),
                "pins": round(row["pins"], 1),
                "games": games,
                "average": round(row["pins"] / games, 2) if games else None,
                "weeks": dict(sorted(history[row["team"]].items())),
            }
        )
    return out


def _series_from_weeks(weekly: list[dict[str, Any]], weeks: list[int]) -> dict[str, Any]:
    teams = sorted({row["team"] for row in weekly})
    by = {(row["team"], row["week"]): row for row in weekly}
    points: dict[str, list[float]] = {team: [] for team in teams}
    averages: dict[str, list[float | None]] = {team: [] for team in teams}
    running: dict[str, float] = {team: 0.0 for team in teams}
    positions: dict[str, list[int]] = {team: [] for team in teams}
    for week in weeks:
        for team in teams:
            row = by.get((team, week))
            pts = float(row["points"]) if row else 0.0
            points[team].append(round(pts, 2))
            averages[team].append(
                round(row["average"], 2) if row and row["average"] is not None else None
            )
            running[team] += pts
        order = sorted(teams, key=lambda name: (-running[name], name))
        place = {name: i for i, name in enumerate(order, start=1)}
        for team in teams:
            positions[team].append(place[team])
    return {
        "categories": weeks,
        "points": points,
        "positions": positions,
        "averages": averages,
    }


def _honor(con, season: str, league: str, week: int) -> dict[str, Any]:
    g = game_line
    scope = event_scope(g, season, league) & (g.week == week)
    individual_scores = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), g.team, g.score)
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.score.is_not_null())
        .order_by(g.score.desc())
        .limit(3),
    )
    team_scores = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.team, g.score)
        .where(scope, is_team_total(g), g.score.is_not_null())
        .order_by(g.score.desc())
        .limit(3),
    )
    individual_averages = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.player_name.as_("player"), g.team, round_(avg_(abs_(g.score)), 2).as_("average"))
        .where(scope, is_player_game(g), ~is_bye(g.player_name))
        .group_by(g.player_name, g.team)
        .order_by(avg_(abs_(g.score)).desc())
        .limit(3),
    )
    team_averages = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.team, round_(avg_(abs_(g.score)), 2).as_("average"))
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.team.is_not_null())
        .group_by(g.team)
        .order_by(avg_(abs_(g.score)).desc())
        .limit(3),
    )
    return {
        "individual_scores": [
            {"player": as_str(r["player"]), "team": as_str(r["team"]), "score": as_int(r["score"])}
            for r in individual_scores
        ],
        "team_scores": [{"team": as_str(r["team"]), "score": as_int(r["score"])} for r in team_scores],
        "individual_averages": [
            {
                "player": as_str(r["player"]),
                "team": as_str(r["team"]),
                "average": as_float(r["average"]),
            }
            for r in individual_averages
        ],
        "team_averages": [
            {"team": as_str(r["team"]), "average": as_float(r["average"])} for r in team_averages
        ],
    }


def _individual_averages(con, season: str, league: str, *, week: int | None = None, team: str | None = None):
    g = game_line
    q = (
        Query()
        .from_(g)
        .select(
            g.player_name.as_("player"),
            g.player_id,
            g.team,
            count_star().as_("games"),
            sum_(abs_(g.score)).as_("pins"),
            round_(avg_(abs_(g.score)), 2).as_("average"),
            max_(abs_(g.score)).as_("high_game"),
        )
        .where(event_scope(g, season, league), is_player_game(g), ~is_bye(g.player_name))
        .group_by(g.player_name, g.player_id, g.team)
        .order_by(avg_(abs_(g.score)).desc())
    )
    if week is not None:
        q = q.where(g.week == week)
    if team:
        q = q.where(g.team == team)
    return [
        {
            "player": as_str(r["player"]),
            "player_id": as_str(r["player_id"]),
            "team": as_str(r["team"]),
            "games": as_int(r["games"]),
            "pins": as_float(r["pins"]),
            "average": as_float(r["average"]),
            "high_game": as_int(r["high_game"]),
        }
        for r in fetch_dicts(con, q)
    ]


def league_standings(season: str, league: str, *, week: int | None = None, view: str | None = None) -> dict[str, Any]:
    with session() as con:
        latest = _latest_week(con, season, league)
        if latest is None:
            return {
                "season": season,
                "league": league,
                "week": None,
                "standings": [],
                "honor_scores": {
                    "individual_scores": [],
                    "team_scores": [],
                    "individual_averages": [],
                    "team_averages": [],
                },
                "series": {"categories": [], "points": {}, "positions": {}, "averages": {}},
                "players": [],
            }
        through = min(week, latest) if week is not None else latest
        weekly = _weekly_rows(con, season, league, through)
        weeks = sorted({row["week"] for row in weekly})
        standings = _standings_from_weeks(weekly)
        payload = {
            "season": season,
            "league": league,
            "week": through,
            "standings": [
                {k: v for k, v in row.items() if k != "weeks" or view == "history"}
                for row in standings
            ],
            "honor_scores": _honor(con, season, league, through),
            "series": _series_from_weeks(weekly, weeks),
            "players": _individual_averages(con, season, league, week=through if view == "averages" else None),
        }
        if view == "history":
            payload["standings"] = standings
        return payload


def season_standings(season: str) -> dict[str, Any]:
    g = game_line
    from bowlyzerapi.engine import fetch_rows

    with session() as con:
        leagues = [
            str(e)
            for (e,) in fetch_rows(
                con,
                Query()
                .from_(g)
                .select(g.event)
                .where(g.season == season, g.event.is_not_null())
                .distinct()
                .order_by(g.event),
            )
        ]
    return {
        "season": season,
        "leagues": [
            {
                "league": name,
                "league_long": name,
                **{k: v for k, v in league_standings(season, name).items() if k not in {"season", "league"}},
            }
            for name in leagues
        ],
    }


def timetable(season: str, league: str) -> dict[str, Any]:
    g = game_line
    with session() as con:
        latest = _latest_week(con, season, league)
        rows = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.week, min_(g.game_date).as_("date"), min_(g.location).as_("location"))
            .where(event_scope(g, season, league), is_team_total(g), g.week.is_not_null())
            .group_by(g.week)
            .order_by(g.week),
        )
    items = []
    for row in rows:
        week = as_int(row["week"])
        status = "pending"
        if latest is not None and week is not None:
            status = "completed" if week <= latest else "pending"
        items.append(
            {
                "week": week,
                "date": as_str(row["date"]),
                "location": as_str(row["location"]),
                "status": status,
            }
        )
    return {"season": season, "league": league, "weeks": items}


def matchday(season: str, league: str, week: int, *, team: str | None = None, round_number: int | None = None) -> dict[str, Any]:
    g = game_line
    with session() as con:
        weekly = _weekly_rows(con, season, league, week)
        standings = [
            {k: v for k, v in row.items() if k != "weeks"}
            for row in _standings_from_weeks(weekly)
        ]
        games_q = (
            Query()
            .from_(g)
            .select(
                g.round_number,
                g.match_number,
                g.team,
                g.opponent,
                g.score.as_("team_pins"),
                g.points.as_("team_match_points"),
                g.bonus_points,
            )
            .where(event_scope(g, season, league), is_team_total(g), g.week == week, g.team.is_not_null())
            .order_by(g.round_number, g.match_number, g.team)
        )
        if round_number is not None:
            games_q = games_q.where(g.round_number == round_number)
        games = fetch_dicts(con, games_q)
        player_pts = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.round_number,
                g.team,
                sum_(g.points).as_("player_points"),
                sum_(abs_(g.score)).as_("player_pins"),
            )
            .where(
                event_scope(g, season, league),
                is_player_game(g),
                g.week == week,
                ~is_bye(g.player_name),
            )
            .group_by(g.round_number, g.team),
        )
        add = {(as_int(r["round_number"]), as_str(r["team"])): r for r in player_pts}
        game_rows = []
        for row in games:
            extra = add.get((as_int(row["round_number"]), as_str(row["team"])), {})
            match_pts = (as_float(row["team_match_points"]) or 0) + (as_float(row["bonus_points"]) or 0)
            game_rows.append(
                {
                    "round": as_int(row["round_number"]),
                    "match": as_int(row["match_number"]),
                    "team": as_str(row["team"]),
                    "opponent": as_str(row["opponent"]),
                    "team_pins": as_float(extra.get("player_pins")) or as_float(row["team_pins"]),
                    "team_points": round(match_pts + (as_float(extra.get("player_points")) or 0), 2),
                }
            )
        details = None
        if team:
            details = _team_week_details(con, season, league, week, team, round_number)
        rounds = sorted({r["round"] for r in game_rows if r["round"] is not None})
        return {
            "season": season,
            "league": league,
            "week": week,
            "rounds": rounds,
            "table": standings,
            "honor_scores": _honor(con, season, league, week),
            "games": game_rows,
            "details": details,
        }


def _team_week_details(con, season, league, week, team, round_number):
    g = game_line
    q = (
        Query()
        .from_(g)
        .select(
            g.round_number,
            g.position,
            g.player_name.as_("player"),
            g.player_id,
            g.opponent,
            g.score,
            g.points,
        )
        .where(
            event_scope(g, season, league),
            is_player_game(g),
            g.week == week,
            g.team == team,
            ~is_bye(g.player_name),
        )
        .order_by(g.round_number, g.position, g.player_name)
    )
    if round_number is not None:
        q = q.where(g.round_number == round_number)
    rows = fetch_dicts(con, q)
    by_player: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = as_str(row["player"]) or ""
        slot = by_player.setdefault(
            name,
            {
                "player": name,
                "player_id": as_str(row["player_id"]),
                "position": as_int(row["position"]),
                "games": [],
                "total_score": 0,
                "total_points": 0.0,
            },
        )
        score = as_int(row["score"]) or 0
        pts = as_float(row["points"]) or 0.0
        slot["games"].append(
            {
                "round": as_int(row["round_number"]),
                "opponent": as_str(row["opponent"]),
                "score": score,
                "points": pts,
            }
        )
        slot["total_score"] += score
        slot["total_points"] += pts
    players = []
    for slot in by_player.values():
        n = len(slot["games"])
        slot["average"] = round(slot["total_score"] / n, 2) if n else None
        slot["total_points"] = round(slot["total_points"], 2)
        players.append(slot)
    players.sort(key=lambda p: (p["position"] is None, p["position"] or 0, p["player"]))
    return {"team": team, "view": "classic", "players": players}


def compare(season: str, league: str, *, team_a: str | None = None, team_b: str | None = None) -> dict[str, Any]:
    g = game_line
    with session() as con:
        rows = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.team,
                g.opponent,
                round_(avg_(abs_(g.score)), 2).as_("avg_pins"),
                round_(avg_(g.points), 2).as_("avg_points"),
                count_star().as_("games"),
            )
            .where(
                event_scope(g, season, league),
                is_player_game(g),
                ~is_bye(g.player_name),
                g.team.is_not_null(),
                g.opponent.is_not_null(),
            )
            .group_by(g.team, g.opponent)
            .order_by(g.team, g.opponent),
        )
    cells = [
        {
            "team": as_str(r["team"]),
            "opponent": as_str(r["opponent"]),
            "avg_pins": as_float(r["avg_pins"]),
            "avg_points": as_float(r["avg_points"]),
            "games": as_int(r["games"]),
        }
        for r in rows
    ]
    if team_a and team_b:
        cells = [c for c in cells if c["team"] == team_a and c["opponent"] == team_b]
    return {"season": season, "league": league, "team_a": team_a, "team_b": team_b, "cells": cells}


def records(season: str | None, league: str, *, metric: str | None = None) -> dict[str, Any]:
    g = game_line
    with session() as con:
        scope = g.event == league
        if season:
            scope = scope & (g.season == season)
        top_team = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.season,
                g.team,
                round_(avg_(abs_(g.score)), 2).as_("average"),
                count_star().as_("games"),
            )
            .where(scope, is_player_game(g), ~is_bye(g.player_name), g.team.is_not_null())
            .group_by(g.season, g.team)
            .order_by(avg_(abs_(g.score)).desc())
            .limit(20),
        )
        top_individual = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.season,
                g.player_name.as_("player"),
                g.team,
                round_(avg_(abs_(g.score)), 2).as_("average"),
                count_star().as_("games"),
            )
            .where(scope, is_player_game(g), ~is_bye(g.player_name))
            .group_by(g.season, g.player_name, g.team)
            .order_by(avg_(abs_(g.score)).desc())
            .limit(30),
        )
        record_individual = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.season, g.week, g.player_name.as_("player"), g.team, g.score, g.game_date)
            .where(scope, is_player_game(g), ~is_bye(g.player_name), g.score.is_not_null())
            .order_by(g.score.desc())
            .limit(15),
        )
        record_team = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.season, g.week, g.team, g.score, g.game_date)
            .where(scope, is_team_total(g), g.score.is_not_null())
            .order_by(g.score.desc())
            .limit(15),
        )
        points_to_win = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.season, g.team, sum_(g.points).as_("points"))
            .where(g.event == league, g.team.is_not_null())
            .group_by(g.season, g.team)
            .order_by(g.season, sum_(g.points).desc()),
        )
        win_by_season: dict[str, dict[str, Any]] = {}
        for row in points_to_win:
            s = as_str(row["season"]) or ""
            pts = as_float(row["points"]) or 0
            current = win_by_season.get(s)
            if current is None or pts > current["points"]:
                win_by_season[s] = {"season": s, "team": as_str(row["team"]), "points": pts}

        averages_history = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.season, round_(avg_(abs_(g.score)), 2).as_("average"))
            .where(g.event == league, is_player_game(g), ~is_bye(g.player_name))
            .group_by(g.season)
            .order_by(g.season),
        )

    payload = {
        "league": league,
        "season": season,
        "averages_history": averages_history,
        "points_to_win": [win_by_season[k] for k in sorted(win_by_season)],
        "top_team": top_team,
        "top_individual": top_individual,
        "record_games": record_individual,
        "record_individual": record_individual,
        "record_team": record_team,
    }
    if metric:
        key = {
            "averages_history": "averages_history",
            "points_to_win": "points_to_win",
            "top_team": "top_team",
            "top_individual": "top_individual",
            "record_games": "record_games",
            "record_individual": "record_individual",
            "record_team": "record_team",
        }.get(metric)
        if key is None:
            return {"league": league, "season": season, "error": {"code": "unknown_metric", "message": metric}}
        return {"league": league, "season": season, "metric": metric, metric: payload[key]}
    return payload
