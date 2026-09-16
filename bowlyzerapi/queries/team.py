"""Team document: history, league comparison, clutch, consistency, special matches."""

from __future__ import annotations

from collections import defaultdict
from math import sqrt
from typing import Any

from bowlyzerapi.engine import (
    Query,
    abs_,
    avg_,
    count_star,
    fetch_dicts,
    fetch_rows,
    game_line,
    max_,
    min_,
    round_,
    sum_,
)
from bowlyzerapi.queries.filters import is_bye, is_player_game, is_team_total
from bowlyzerapi.queries.league import league_standings
from bowlyzerapi.queries.util import as_float, as_int, as_str
from bowlyzerapi.warehouse import session


def team_list() -> dict[str, Any]:
    g = game_line
    with session() as con:
        rows = fetch_rows(
            con,
            Query()
            .from_(g)
            .select(g.team)
            .where(is_player_game(g), g.team.is_not_null())
            .distinct()
            .order_by(g.team),
        )
    return {"teams": [str(n) for (n,) in rows]}


def _matches(con, team: str, season: str | None) -> list[dict[str, Any]]:
    g = game_line
    a = g.as_("a")
    b = g.as_("b")
    q = (
        Query()
        .from_(a)
        .join(
            b,
            on=(
                (b.team == a.opponent)
                & (b.opponent == a.team)
                & (b.season == a.season)
                & (b.event == a.event)
                & (b.week == a.week)
                & (b.round_number == a.round_number)
                & b.computed_data.is_true()
            ),
        )
        .select(
            a.season,
            a.event,
            a.week,
            a.round_number,
            a.score,
            b.score.as_("opp_score"),
            a.opponent,
        )
        .where(is_team_total(a), a.team == team, a.round_number > 0, a.score.is_not_null(), b.score.is_not_null())
    )
    if season:
        q = q.where(a.season == season)
    return fetch_dicts(con, q)


def team_document(team: str, *, season: str | None = None) -> dict[str, Any]:
    g = game_line
    with session() as con:
        seasons = [
            str(s)
            for (s,) in fetch_rows(
                con,
                Query()
                .from_(g)
                .select(g.season)
                .where(g.team == team, g.season.is_not_null())
                .distinct()
                .order_by(g.season),
            )
        ]
        history_rows = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.season,
                g.event,
                count_star().as_("games"),
                sum_(abs_(g.score)).as_("total_score"),
                sum_(g.points).as_("total_points"),
                round_(avg_(abs_(g.score)), 2).as_("average_score"),
                max_(abs_(g.score)).as_("best_score"),
                min_(abs_(g.score)).as_("worst_score"),
            )
            .where(is_player_game(g), ~is_bye(g.player_name), g.team == team)
            .group_by(g.season, g.event)
            .order_by(g.season, g.event),
        )
        matches = _matches(con, team, season)

    history: dict[str, Any] = {}
    for row in history_rows:
        s = as_str(row["season"]) or ""
        league = as_str(row["event"]) or ""
        standings = league_standings(s, league)
        pos = next((r["rank"] for r in standings["standings"] if r["team"] == team), None)
        history[s] = {
            "league_name": league,
            "final_position": pos,
            "statistics": {
                "total_score": as_float(row["total_score"]),
                "total_points": as_float(row["total_points"]),
                "average_score": as_float(row["average_score"]),
                "games_played": as_int(row["games"]),
                "best_score": as_int(row["best_score"]),
                "worst_score": as_int(row["worst_score"]),
            },
        }

    leagues: dict[str, Any] = {}
    for s, block in history.items():
        league = block["league_name"]
        standings = league_standings(s, league)
        team_row = next((r for r in standings["standings"] if r["team"] == team), None)
        field_avg = None
        if standings["standings"]:
            avgs = [r["average"] for r in standings["standings"] if r["average"] is not None]
            field_avg = round(sum(avgs) / len(avgs), 2) if avgs else None
        leagues[s] = {
            "league_name": league,
            "num_teams": len(standings["standings"]),
            "team_average": team_row["average"] if team_row else None,
            "league_average": field_avg,
            "vs_league_average": (
                round(team_row["average"] - field_avg, 2)
                if team_row and team_row["average"] is not None and field_avg is not None
                else None
            ),
            "final_position": block["final_position"],
        }

    clutch = _clutch(matches)
    consistency = _consistency([as_float(m["score"]) or 0 for m in matches])
    special = _special(matches)
    return {
        "team": team,
        "season": season,
        "seasons": seasons,
        "history": history,
        "leagues": leagues,
        "clutch": clutch,
        "consistency": consistency,
        "special_matches": special,
    }


def _clutch(matches: list[dict[str, Any]], threshold: float = 10) -> dict[str, Any]:
    clutch_rows = []
    by_opp: dict[str, dict[str, int]] = defaultdict(lambda: {"wins": 0, "losses": 0})
    wins = losses = 0
    for row in matches:
        score = as_float(row["score"]) or 0
        opp = as_float(row["opp_score"]) or 0
        margin = score - opp
        if abs(margin) >= threshold:
            continue
        clutch_rows.append(row)
        opp_name = as_str(row["opponent"]) or ""
        if margin > 0:
            wins += 1
            by_opp[opp_name]["wins"] += 1
        elif margin < 0:
            losses += 1
            by_opp[opp_name]["losses"] += 1
    n = len(clutch_rows)
    return {
        "total_games": len(matches),
        "total_clutch_games": n,
        "total_clutch_wins": wins,
        "total_clutch_losses": losses,
        "clutch_percentage": round(100 * wins / n, 1) if n else None,
        "opponent_clutch": dict(by_opp),
    }


def _consistency(scores: list[float]) -> dict[str, Any]:
    n = len(scores)
    if n == 0:
        return {"sample": 0}
    mean = sum(scores) / n
    var = sum((x - mean) ** 2 for x in scores) / n
    std = sqrt(var)
    cv = (std / mean * 100) if mean else None
    ordered = sorted(scores)
    q1 = ordered[n // 4]
    q3 = ordered[(3 * n) // 4]
    if cv is None:
        rating = None
    elif cv <= 5:
        rating = "Excellent"
    elif cv <= 10:
        rating = "Good"
    elif cv <= 15:
        rating = "Average"
    elif cv <= 20:
        rating = "Below Average"
    else:
        rating = "Inconsistent"
    return {
        "sample": n,
        "mean": round(mean, 2),
        "std": round(std, 2),
        "cv_percent": round(cv, 2) if cv is not None else None,
        "min": min(scores),
        "max": max(scores),
        "range": max(scores) - min(scores),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "consistency_rating": rating,
    }


def _special(matches: list[dict[str, Any]]) -> dict[str, Any]:
    def row(m: dict[str, Any]) -> dict[str, Any]:
        score = as_float(m["score"]) or 0
        opp = as_float(m["opp_score"]) or 0
        return {
            "season": as_str(m["season"]),
            "league": as_str(m["event"]),
            "week": as_int(m["week"]),
            "round": as_int(m["round_number"]),
            "score": score,
            "opponent": as_str(m["opponent"]),
            "opponent_score": opp,
            "win_margin": score - opp,
        }

    packed = [row(m) for m in matches]
    wins = [r for r in packed if r["win_margin"] > 0]
    losses = [r for r in packed if r["win_margin"] < 0]
    return {
        "highest_scores": sorted(packed, key=lambda r: -r["score"])[:5],
        "lowest_scores": sorted(packed, key=lambda r: r["score"])[:5],
        "biggest_win_margin": sorted(wins, key=lambda r: -r["win_margin"])[:5],
        "biggest_loss_margin": sorted(losses, key=lambda r: r["win_margin"])[:5],
    }
