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
    or_,
    round_,
    sum_,
)
from bowlyzerapi.queries.filters import (
    event_scope,
    is_bye,
    is_player_game,
    is_team_total,
)
from bowlyzerapi.queries.identity import apply_canonical_player_names
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


def _latest_weeks(con, season: str) -> dict[str, int]:
    g = game_line
    rows = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.event, max_(g.week).as_("week"))
        .where(g.season == season, g.event.is_not_null(), g.week.is_not_null())
        .group_by(g.event),
    )
    out: dict[str, int] = {}
    for row in rows:
        event = as_str(row["event"]) or ""
        week = as_int(row["week"])
        if event and week is not None:
            out[event] = week
    return out


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


def _pair_scope(g, pairs: list[tuple[str, str]]):
    return or_(*[event_scope(g, season, event) for season, event in pairs])


def league_table_snapshots(
    con,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Final table per (season, event), same ranking as `league_standings` through latest week.

    Season-level totals match summing weekly rows. Honor / series / players are omitted.
    """
    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for season, event in pairs:
        key = (as_str(season) or "", as_str(event) or "")
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        unique.append(key)
    if not unique:
        return {}

    g = game_line
    scope = _pair_scope(g, unique) & g.week.is_not_null()
    players = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.season,
            g.event,
            g.team,
            sum_(abs_(g.score)).as_("pins"),
            sum_(g.points).as_("player_pts"),
            count_star().as_("games"),
        )
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.team.is_not_null())
        .group_by(g.season, g.event, g.team),
    )
    teams = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.season,
            g.event,
            g.team,
            (sum_(g.points) + coalesce(sum_(g.bonus_points), 0)).as_("match_pts"),
        )
        .where(scope, is_team_total(g), g.team.is_not_null())
        .group_by(g.season, g.event, g.team),
    )

    totals: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in players:
        key = (str(row["season"]), str(row["event"]), str(row["team"]))
        totals[key] = {
            "team": key[2],
            "points": as_float(row["player_pts"]) or 0.0,
            "pins": as_float(row["pins"]) or 0.0,
            "games": as_int(row["games"]) or 0,
        }
    for row in teams:
        key = (str(row["season"]), str(row["event"]), str(row["team"]))
        slot = totals.setdefault(key, {"team": key[2], "points": 0.0, "pins": 0.0, "games": 0})
        slot["points"] += as_float(row["match_pts"]) or 0.0

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for season, event, _team in totals:
        grouped[(season, event)].append(totals[(season, event, _team)])

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in unique:
        ranked = sorted(grouped.get(pair, []), key=lambda r: (-r["points"], -r["pins"], r["team"]))
        standings = []
        for i, row in enumerate(ranked, start=1):
            games = row["games"] or 0
            standings.append(
                {
                    "rank": i,
                    "team": row["team"],
                    "points": round(row["points"], 2),
                    "pins": round(row["pins"], 1),
                    "games": games,
                    "average": round(row["pins"] / games, 2) if games else None,
                }
            )
        avgs = [r["average"] for r in standings if r["average"] is not None]
        field_avg = round(sum(avgs) / len(avgs), 2) if avgs else None
        out[pair] = {
            "standings": standings,
            "num_teams": len(standings),
            "league_average": field_avg,
            "by_team": {r["team"]: r for r in standings},
        }
    for pair in unique:
        out.setdefault(
            pair,
            {"standings": [], "num_teams": 0, "league_average": None, "by_team": {}},
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
        .select(g.player_name.as_("player"), g.player_id, g.team, g.score, g.round_number, g.match_number)
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.score.is_not_null())
        .order_by(g.score.desc(), g.player_name, g.team, g.round_number, g.match_number)
        .limit(3),
    )
    team_scores = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.team, g.score)
        .where(scope, is_team_total(g), g.score.is_not_null())
        .order_by(g.score.desc(), g.team)
        .limit(3),
    )
    individual_averages = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.player_name.as_("player"),
            g.player_id,
            g.team,
            round_(avg_(abs_(g.score)), 2).as_("average"),
        )
        .where(scope, is_player_game(g), ~is_bye(g.player_name))
        .group_by(g.player_name, g.player_id, g.team)
        .order_by(avg_(abs_(g.score)).desc(), g.player_name, g.team)
        .limit(3),
    )
    team_averages = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.team, round_(avg_(abs_(g.score)), 2).as_("average"))
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.team.is_not_null())
        .group_by(g.team)
        .order_by(avg_(abs_(g.score)).desc(), g.team)
        .limit(3),
    )
    scores = [
        {
            "player": as_str(r["player"]),
            "player_id": as_str(r.get("player_id")),
            "team": as_str(r["team"]),
            "score": as_int(r["score"]),
            "round": as_int(r.get("round_number")),
            "game": as_int(r.get("match_number")),
        }
        for r in individual_scores
    ]
    averages = [
        {
            "player": as_str(r["player"]),
            "player_id": as_str(r.get("player_id")),
            "team": as_str(r["team"]),
            "average": as_float(r["average"]),
        }
        for r in individual_averages
    ]
    apply_canonical_player_names(con, scores, name_key="player")
    apply_canonical_player_names(con, averages, name_key="player")
    return {
        "individual_scores": scores,
        "team_scores": [{"team": as_str(r["team"]), "score": as_int(r["score"])} for r in team_scores],
        "individual_averages": averages,
        "team_averages": [
            {"team": as_str(r["team"]), "average": as_float(r["average"])} for r in team_averages
        ],
    }


def _empty_honor() -> dict[str, Any]:
    return {
        "individual_scores": [],
        "team_scores": [],
        "individual_averages": [],
        "team_averages": [],
    }


def _honor_snapshots(con, season: str, weeks: dict[str, int]) -> dict[str, dict[str, Any]]:
    """Honor scores for each league's latest week — same lists as `_honor`."""
    out = {event: _empty_honor() for event in weeks}
    if not weeks:
        return out
    g = game_line
    scope = (g.season == season) & or_(
        *[(g.event == event) & (g.week == week) for event, week in weeks.items()]
    )
    individual_scores = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.event,
            g.player_name.as_("player"),
            g.player_id,
            g.team,
            g.score,
            g.round_number,
            g.match_number,
        )
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.score.is_not_null()),
    )
    team_scores = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.event, g.team, g.score)
        .where(scope, is_team_total(g), g.score.is_not_null()),
    )
    individual_averages = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(
            g.event,
            g.player_name.as_("player"),
            g.player_id,
            g.team,
            round_(avg_(abs_(g.score)), 2).as_("average"),
        )
        .where(scope, is_player_game(g), ~is_bye(g.player_name))
        .group_by(g.event, g.player_name, g.player_id, g.team),
    )
    team_averages = fetch_dicts(
        con,
        Query()
        .from_(g)
        .select(g.event, g.team, round_(avg_(abs_(g.score)), 2).as_("average"))
        .where(scope, is_player_game(g), ~is_bye(g.player_name), g.team.is_not_null())
        .group_by(g.event, g.team),
    )

    scores_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in individual_scores:
        event = as_str(row["event"]) or ""
        scores_by[event].append(
            {
                "player": as_str(row["player"]),
                "player_id": as_str(row.get("player_id")),
                "team": as_str(row["team"]),
                "score": as_int(row["score"]),
                "round": as_int(row.get("round_number")),
                "game": as_int(row.get("match_number")),
            }
        )
    team_scores_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in team_scores:
        event = as_str(row["event"]) or ""
        team_scores_by[event].append({"team": as_str(row["team"]), "score": as_int(row["score"])})
    averages_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in individual_averages:
        event = as_str(row["event"]) or ""
        averages_by[event].append(
            {
                "player": as_str(row["player"]),
                "player_id": as_str(row.get("player_id")),
                "team": as_str(row["team"]),
                "average": as_float(row["average"]),
            }
        )
    team_avgs_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in team_averages:
        event = as_str(row["event"]) or ""
        team_avgs_by[event].append({"team": as_str(row["team"]), "average": as_float(row["average"])})

    all_scores: list[dict[str, Any]] = []
    all_averages: list[dict[str, Any]] = []
    for event in weeks:
        scores = sorted(
            scores_by.get(event, []),
            key=lambda r: (
                -(r["score"] or 0),
                r.get("player") or "",
                r.get("team") or "",
                r.get("round") or 0,
                r.get("game") or 0,
            ),
        )[:3]
        averages = sorted(
            averages_by.get(event, []),
            key=lambda r: (-(r["average"] or 0), r.get("player") or "", r.get("team") or ""),
        )[:3]
        out[event] = {
            "individual_scores": scores,
            "team_scores": sorted(
                team_scores_by.get(event, []),
                key=lambda r: (-(r["score"] or 0), r.get("team") or ""),
            )[:3],
            "individual_averages": averages,
            "team_averages": sorted(
                team_avgs_by.get(event, []),
                key=lambda r: (-(r["average"] or 0), r.get("team") or ""),
            )[:3],
        }
        all_scores.extend(scores)
        all_averages.extend(averages)
    apply_canonical_player_names(con, all_scores, name_key="player")
    apply_canonical_player_names(con, all_averages, name_key="player")
    return out


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
    rows = [
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
    apply_canonical_player_names(con, rows, name_key="player")
    return rows


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
    with session() as con:
        leagues = [
            str(row["event"])
            for row in fetch_dicts(
                con,
                Query()
                .from_(g)
                .select(g.event)
                .where(g.season == season, g.event.is_not_null())
                .distinct()
                .order_by(g.event),
            )
        ]
        snaps = league_table_snapshots(con, [(season, name) for name in leagues])
        weeks = _latest_weeks(con, season)
        honors = _honor_snapshots(con, season, weeks)
    return {
        "season": season,
        "leagues": [
            {
                "league": name,
                "league_long": name,
                "week": weeks.get(name),
                "standings": (snaps.get((season, name)) or {}).get("standings") or [],
                "honor_scores": honors.get(name) or _empty_honor(),
                "series": {"categories": [], "points": {}, "positions": {}, "averages": {}},
                "players": [],
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


def _normalize_matchday_view(view: str | None) -> str:
    raw = (view or "classic").strip().lower().replace("-", "_")
    if raw in {"headtohead", "head_to_head", "h2h"}:
        return "h2h"
    if raw == "individual":
        return "individual"
    return "classic"


def _player_lines(
    con,
    season: str,
    league: str,
    week: int,
    *,
    team: str | None = None,
    facing: str | None = None,
    round_number: int | None = None,
) -> list[dict[str, Any]]:
    g = game_line
    q = (
        Query()
        .from_(g)
        .select(
            g.round_number,
            g.match_number,
            g.position,
            g.player_name.as_("player"),
            g.player_id,
            g.team,
            g.opponent,
            g.score,
            g.points,
        )
        .where(
            event_scope(g, season, league),
            is_player_game(g),
            g.week == week,
            ~is_bye(g.player_name),
        )
        .order_by(g.round_number, g.position, g.player_name)
    )
    if team:
        q = q.where(g.team == team)
    if facing:
        q = q.where(g.opponent == facing)
    if round_number is not None:
        q = q.where(g.round_number == round_number)
    rows = [
        {
            "round": as_int(r["round_number"]),
            "match": as_int(r["match_number"]),
            "position": as_int(r["position"]),
            "player": as_str(r["player"]),
            "player_id": as_str(r["player_id"]),
            "team": as_str(r["team"]),
            "opponent": as_str(r["opponent"]),
            "score": as_int(r["score"]),
            "points": as_float(r["points"]),
        }
        for r in fetch_dicts(con, q)
    ]
    apply_canonical_player_names(con, rows, name_key="player")
    return rows


def _team_totals(con, season: str, league: str, week: int, *, team: str | None = None):
    g = game_line
    q = (
        Query()
        .from_(g)
        .select(
            g.round_number,
            g.match_number,
            g.team,
            g.opponent,
            g.score,
            g.points,
            g.bonus_points,
        )
        .where(event_scope(g, season, league), is_team_total(g), g.week == week, g.team.is_not_null())
        .order_by(g.round_number, g.team)
    )
    if team:
        q = q.where(g.team == team)
    return [
        {
            "round": as_int(r["round_number"]),
            "match": as_int(r["match_number"]),
            "team": as_str(r["team"]),
            "opponent": as_str(r["opponent"]),
            "score": as_float(r["score"]),
            "points": (as_float(r["points"]) or 0.0) + (as_float(r["bonus_points"]) or 0.0),
        }
        for r in fetch_dicts(con, q)
    ]


def matchday(
    season: str,
    league: str,
    week: int,
    *,
    team: str | None = None,
    round_number: int | None = None,
    view: str | None = None,
) -> dict[str, Any]:
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
        game_details = None
        resolved_view = _normalize_matchday_view(view)
        if team:
            details = _team_week_details(con, season, league, week, team, round_number, resolved_view)
            if round_number is not None:
                game_details = _game_details(con, season, league, week, team, round_number)
        rounds = sorted({r["round"] for r in game_rows if r["round"] is not None})
        return {
            "season": season,
            "league": league,
            "week": week,
            "view": resolved_view if team else None,
            "rounds": rounds,
            "table": standings,
            "honor_scores": _honor(con, season, league, week),
            "games": game_rows,
            "players": _individual_averages(con, season, league, week=week, team=team),
            "details": details,
            "game_details": game_details,
        }


def _team_week_details(con, season, league, week, team, round_number, view: str = "classic"):
    if view == "individual":
        return _team_week_individual(con, season, league, week, team)
    if view == "h2h":
        return _team_week_h2h(con, season, league, week, team)
    lines = _player_lines(con, season, league, week, team=team, round_number=round_number)
    by_player: dict[str, dict[str, Any]] = {}
    for row in lines:
        key = row["player_id"] or row["player"] or ""
        slot = by_player.setdefault(
            key,
            {
                "player": row["player"],
                "player_id": row["player_id"],
                "position": row["position"],
                "games": [],
                "total_score": 0,
                "total_points": 0.0,
            },
        )
        score = row["score"] or 0
        pts = row["points"] or 0.0
        slot["games"].append(
            {
                "round": row["round"],
                "opponent": row["opponent"],
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
    players.sort(key=lambda p: (p["position"] is None, p["position"] or 0, p["player"] or ""))
    return {"team": team, "view": "classic", "players": players}


def _player_key(row: dict[str, Any]) -> str:
    return row.get("player_id") or row.get("player") or ""


def _team_week_individual(con, season, league, week, team):
    lines = _player_lines(con, season, league, week, team=team)
    totals = {row["round"]: row for row in _team_totals(con, season, league, week, team=team)}
    players: dict[str, dict[str, Any]] = {}
    for row in lines:
        key = _player_key(row)
        players.setdefault(
            key,
            {"player": row["player"], "player_id": row["player_id"], "key": key},
        )
    player_list = sorted(players.values(), key=lambda p: (p["player"] or "", p["player_id"] or ""))
    rounds: dict[int, dict[str, Any]] = {}
    for row in lines:
        rnd = row["round"]
        if rnd is None:
            continue
        slot = rounds.setdefault(
            rnd,
            {
                "round": rnd,
                "opponent": row["opponent"],
                "team_score": None,
                "team_points": None,
                "players": {},
            },
        )
        slot["players"][_player_key(row)] = {
            "player": row["player"],
            "player_id": row["player_id"],
            "position": row["position"],
            "score": row["score"],
            "points": row["points"],
        }
    for rnd, total in totals.items():
        if rnd is None:
            continue
        slot = rounds.setdefault(
            rnd,
            {
                "round": rnd,
                "opponent": total["opponent"],
                "team_score": None,
                "team_points": None,
                "players": {},
            },
        )
        slot["team_score"] = total["score"]
        slot["team_points"] = total["points"]
        if not slot["opponent"]:
            slot["opponent"] = total["opponent"]
    ordered = []
    for rnd in sorted(rounds):
        item = rounds[rnd]
        ordered.append(
            {
                "round": rnd,
                "opponent": item["opponent"],
                "team_score": item["team_score"],
                "team_points": item["team_points"],
                "players": [item["players"].get(p["key"]) for p in player_list],
            }
        )
    return {"team": team, "view": "individual", "players": player_list, "rounds": ordered}


def _team_week_h2h(con, season, league, week, team):
    own = _player_lines(con, season, league, week, team=team)
    opp = _player_lines(con, season, league, week, facing=team)
    totals = {row["round"]: row for row in _team_totals(con, season, league, week, team=team)}
    own_players: dict[str, dict[str, Any]] = {}
    opp_players: dict[str, dict[str, Any]] = {}
    for row in own:
        own_players.setdefault(
            _player_key(row),
            {"player": row["player"], "player_id": row["player_id"], "key": _player_key(row)},
        )
    for row in opp:
        opp_players.setdefault(
            _player_key(row),
            {"player": row["player"], "player_id": row["player_id"], "key": _player_key(row)},
        )
    own_list = sorted(own_players.values(), key=lambda p: (p["player"] or "", p["player_id"] or ""))
    opp_list = sorted(opp_players.values(), key=lambda p: (p["player"] or "", p["player_id"] or ""))
    rounds: dict[int, dict[str, Any]] = {}
    for row in own:
        rnd = row["round"]
        if rnd is None:
            continue
        slot = rounds.setdefault(
            rnd,
            {
                "round": rnd,
                "opponent": row["opponent"],
                "team_score": None,
                "team_points": None,
                "own": {},
                "opp": {},
            },
        )
        slot["own"][_player_key(row)] = {
            "player": row["player"],
            "player_id": row["player_id"],
            "position": row["position"],
            "score": row["score"],
            "points": row["points"],
        }
    for row in opp:
        rnd = row["round"]
        if rnd is None:
            continue
        slot = rounds.setdefault(
            rnd,
            {
                "round": rnd,
                "opponent": row["team"],
                "team_score": None,
                "team_points": None,
                "own": {},
                "opp": {},
            },
        )
        if not slot["opponent"]:
            slot["opponent"] = row["team"]
        slot["opp"][_player_key(row)] = {
            "player": row["player"],
            "player_id": row["player_id"],
            "position": row["position"],
            "score": row["score"],
            "points": row["points"],
        }
    for rnd, total in totals.items():
        if rnd is None:
            continue
        slot = rounds.setdefault(
            rnd,
            {
                "round": rnd,
                "opponent": total["opponent"],
                "team_score": None,
                "team_points": None,
                "own": {},
                "opp": {},
            },
        )
        slot["team_score"] = total["score"]
        slot["team_points"] = total["points"]
    ordered = []
    for rnd in sorted(rounds):
        item = rounds[rnd]
        ordered.append(
            {
                "round": rnd,
                "opponent": item["opponent"],
                "team_score": item["team_score"],
                "team_points": item["team_points"],
                "own": [item["own"].get(p["key"]) for p in own_list],
                "opp": [item["opp"].get(p["key"]) for p in opp_list],
            }
        )
    return {
        "team": team,
        "view": "h2h",
        "own_players": own_list,
        "opp_players": opp_list,
        "rounds": ordered,
    }


def _game_details(con, season, league, week, team, round_number):
    own = _player_lines(con, season, league, week, team=team, round_number=round_number)
    opponent = next((row["opponent"] for row in own if row["opponent"]), None)
    opp = (
        _player_lines(con, season, league, week, team=opponent, round_number=round_number)
        if opponent
        else []
    )
    own_by_pos = {row["position"]: row for row in own if row["position"] is not None}
    opp_by_pos = {row["position"]: row for row in opp if row["position"] is not None}
    positions = sorted(set(own_by_pos) | set(opp_by_pos))
    players = []
    for pos in positions:
        left = own_by_pos.get(pos)
        right = opp_by_pos.get(pos)
        players.append(
            {
                "position": pos,
                "player": left["player"] if left else None,
                "player_id": left["player_id"] if left else None,
                "pins": left["score"] if left else None,
                "points": left["points"] if left else None,
                "opponent_player": right["player"] if right else None,
                "opponent_player_id": right["player_id"] if right else None,
                "opponent_pins": right["score"] if right else None,
                "opponent_points": right["points"] if right else None,
            }
        )
    return {"team": team, "opponent": opponent, "round": round_number, "players": players}


def compare(
    season: str,
    league: str,
    *,
    team_a: str | None = None,
    team_b: str | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    g = game_line
    with session() as con:
        latest = _latest_week(con, season, league)
        standings_order = []
        if latest is not None:
            through = min(week, latest) if week is not None else latest
            standings_order = [row["team"] for row in _standings_from_weeks(_weekly_rows(con, season, league, through))]
        totals_q = (
            Query()
            .from_(g)
            .select(
                g.team,
                g.opponent,
                g.week,
                g.round_number,
                abs_(g.score).as_("pins"),
                (g.points + coalesce(g.bonus_points, 0)).as_("match_points"),
            )
            .where(
                event_scope(g, season, league),
                is_team_total(g),
                g.team.is_not_null(),
                g.opponent.is_not_null(),
            )
        )
        players_q = (
            Query()
            .from_(g)
            .select(
                g.team,
                g.opponent,
                g.week,
                g.round_number,
                sum_(g.points).as_("player_points"),
            )
            .where(
                event_scope(g, season, league),
                is_player_game(g),
                ~is_bye(g.player_name),
                g.team.is_not_null(),
                g.opponent.is_not_null(),
            )
            .group_by(g.team, g.opponent, g.week, g.round_number)
        )
        if week is not None:
            totals_q = totals_q.where(g.week == week)
            players_q = players_q.where(g.week == week)
        totals = fetch_dicts(con, totals_q)
        player_pts = {
            (
                as_str(r["team"]),
                as_str(r["opponent"]),
                as_int(r["week"]),
                as_int(r["round_number"]),
            ): as_float(r["player_points"]) or 0.0
            for r in fetch_dicts(con, players_q)
        }

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in totals:
        team = as_str(row["team"]) or ""
        opponent = as_str(row["opponent"]) or ""
        if not team or not opponent:
            continue
        key = (team, opponent)
        slot = buckets.setdefault(
            key,
            {"team": team, "opponent": opponent, "pins": [], "points": [], "weeks": []},
        )
        pins = as_float(row["pins"]) or 0.0
        match_pts = as_float(row["match_points"]) or 0.0
        extra = player_pts.get(
            (team, opponent, as_int(row["week"]), as_int(row["round_number"])),
            0.0,
        )
        slot["pins"].append(pins)
        slot["points"].append(match_pts + extra)
        week_n = as_int(row["week"])
        if week_n is not None:
            slot["weeks"].append(week_n)

    cells = []
    for slot in buckets.values():
        n = len(slot["pins"])
        cells.append(
            {
                "team": slot["team"],
                "opponent": slot["opponent"],
                "avg_pins": round(sum(slot["pins"]) / n, 2) if n else None,
                "avg_points": round(sum(slot["points"]) / n, 2) if n else None,
                "games": n,
                "week": max(slot["weeks"]) if slot["weeks"] else None,
            }
        )
    cells.sort(key=lambda c: (c["team"], c["opponent"]))
    if team_a and team_b:
        cells = [c for c in cells if c["team"] == team_a and c["opponent"] == team_b]
    return {
        "season": season,
        "league": league,
        "week": week,
        "team_a": team_a,
        "team_b": team_b,
        "teams": standings_order,
        "cells": cells,
    }


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
                g.player_id,
                g.team,
                round_(avg_(abs_(g.score)), 2).as_("average"),
                count_star().as_("games"),
            )
            .where(scope, is_player_game(g), ~is_bye(g.player_name))
            .group_by(g.season, g.player_name, g.player_id, g.team)
            .order_by(avg_(abs_(g.score)).desc())
            .limit(30),
        )
        record_individual = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(
                g.season,
                g.week,
                g.player_name.as_("player"),
                g.player_id,
                g.team,
                g.score,
                g.game_date,
            )
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

        apply_canonical_player_names(con, top_individual, name_key="player")
        apply_canonical_player_names(con, record_individual, name_key="player")

    payload = {
        "league": league,
        "season": season,
        "averages_history": [
            {"season": as_str(r["season"]), "average": as_float(r["average"])} for r in averages_history
        ],
        "points_to_win": [win_by_season[k] for k in sorted(win_by_season)],
        "top_team": [
            {
                "season": as_str(r["season"]),
                "team": as_str(r["team"]),
                "average": as_float(r["average"]),
                "games": as_int(r["games"]),
                "league": league,
            }
            for r in top_team
        ],
        "top_individual": [
            {
                "season": as_str(r["season"]),
                "player": as_str(r["player"]),
                "player_id": as_str(r.get("player_id")),
                "team": as_str(r["team"]),
                "average": as_float(r["average"]),
                "games": as_int(r["games"]),
                "league": league,
            }
            for r in top_individual
        ],
        "record_games": [
            {
                "season": as_str(r["season"]),
                "week": as_int(r["week"]),
                "player": as_str(r["player"]),
                "player_id": as_str(r.get("player_id")),
                "team": as_str(r["team"]),
                "score": as_int(r["score"]),
                "league": league,
            }
            for r in record_individual
        ],
        "record_individual": [
            {
                "season": as_str(r["season"]),
                "week": as_int(r["week"]),
                "player": as_str(r["player"]),
                "player_id": as_str(r.get("player_id")),
                "team": as_str(r["team"]),
                "score": as_int(r["score"]),
                "league": league,
            }
            for r in record_individual
        ],
        "record_team": [
            {
                "season": as_str(r["season"]),
                "week": as_int(r["week"]),
                "team": as_str(r["team"]),
                "score": as_int(r["score"]),
                "league": league,
            }
            for r in record_team
        ],
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


def team_in_league(season: str, league: str, team: str) -> dict[str, Any]:
    g = game_line
    with session() as con:
        ours = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.week, g.round_number, g.position, g.player_name.as_("player"), g.player_id, g.score)
            .where(
                event_scope(g, season, league),
                is_player_game(g),
                g.team == team,
                ~is_bye(g.player_name),
            )
            .order_by(g.week, g.round_number, g.position),
        )
        opp = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.week, g.round_number, g.position, g.score)
            .where(
                event_scope(g, season, league),
                is_player_game(g),
                g.opponent == team,
                ~is_bye(g.player_name),
            ),
        )
        team_totals = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.week, g.round_number, g.opponent, abs_(g.score).as_("score"))
            .where(event_scope(g, season, league), is_team_total(g), g.team == team),
        )
        opp_totals = fetch_dicts(
            con,
            Query()
            .from_(g)
            .select(g.week, g.round_number, g.team, abs_(g.score).as_("score"))
            .where(event_scope(g, season, league), is_team_total(g), g.opponent == team),
        )
        apply_canonical_player_names(con, ours, name_key="player")

    opp_by_slot = {
        (as_int(r["week"]), as_int(r["round_number"]), as_int(r["position"])): as_int(r["score"])
        for r in opp
    }
    opp_total_by_slot = {
        (as_int(r["week"]), as_int(r["round_number"]), as_str(r["team"])): as_float(r["score"]) or 0.0
        for r in opp_totals
    }

    weeks = sorted({as_int(r["week"]) for r in ours if as_int(r["week"]) is not None})
    players = sorted({as_str(r["player"]) or "" for r in ours if as_str(r["player"])})

    def _empty_weeks() -> list[float | None]:
        return [None] * len(weeks)

    week_index = {w: i for i, w in enumerate(weeks)}
    perf: dict[str, list[float | None]] = {name: _empty_weeks() for name in players}
    pins: dict[str, list[float]] = {name: [0.0] * len(weeks) for name in players}
    games: dict[str, list[int]] = {name: [0] * len(weeks) for name in players}
    wins: dict[str, list[int]] = {name: [0] * len(weeks) for name in players}
    matches: dict[str, list[int]] = {name: [0] * len(weeks) for name in players}

    for row in ours:
        name = as_str(row["player"]) or ""
        week_n = as_int(row["week"])
        if not name or week_n not in week_index:
            continue
        idx = week_index[week_n]
        score = as_int(row["score"]) or 0
        pins[name][idx] += score
        games[name][idx] += 1
        opp_score = opp_by_slot.get((week_n, as_int(row["round_number"]), as_int(row["position"])))
        if opp_score is not None:
            matches[name][idx] += 1
            if score > opp_score:
                wins[name][idx] += 1

    for name in players:
        for idx, n in enumerate(games[name]):
            if n:
                perf[name][idx] = round(pins[name][idx] / n, 2)

    team_perf = _empty_weeks()
    team_pins = [0.0] * len(weeks)
    team_games = [0] * len(weeks)
    for name in players:
        for idx in range(len(weeks)):
            team_pins[idx] += pins[name][idx]
            team_games[idx] += games[name][idx]
    for idx, n in enumerate(team_games):
        if n:
            team_perf[idx] = round(team_pins[idx] / n, 2)

    win_pct: dict[str, list[float | None]] = {name: _empty_weeks() for name in players}
    for name in players:
        for idx, n in enumerate(matches[name]):
            if n:
                win_pct[name][idx] = round(100.0 * wins[name][idx] / n, 1)

    team_win = _empty_weeks()
    team_week_wins = [0] * len(weeks)
    team_week_matches = [0] * len(weeks)
    for row in team_totals:
        week_n = as_int(row["week"])
        if week_n not in week_index:
            continue
        idx = week_index[week_n]
        opponent = as_str(row["opponent"])
        ours_score = as_float(row["score"]) or 0.0
        theirs = opp_total_by_slot.get((week_n, as_int(row["round_number"]), opponent))
        if theirs is None:
            continue
        team_week_matches[idx] += 1
        if ours_score > theirs:
            team_week_wins[idx] += 1
    for idx, n in enumerate(team_week_matches):
        if n:
            team_win[idx] = round(100.0 * team_week_wins[idx] / n, 1)

    def _series(data: dict[str, list], *, totals: dict[str, float], averages: dict[str, float], counts: dict[str, int]):
        keys = list(data)
        return {
            "data": data,
            "total": totals,
            "average": averages,
            "counts": counts,
            "sorted_by_average": sorted(keys, key=lambda k: averages.get(k, 0), reverse=True),
            "sorted_by_total": sorted(keys, key=lambda k: totals.get(k, 0), reverse=True),
        }

    perf_data = {**perf, team: team_perf}
    win_data = {**win_pct, team: team_win}
    perf_totals = {name: round(sum(pins[name]), 2) for name in players}
    perf_totals[team] = round(sum(team_pins), 2)
    perf_counts = {name: sum(games[name]) for name in players}
    perf_counts[team] = sum(team_games)
    perf_avg = {
        name: round(perf_totals[name] / perf_counts[name], 2) if perf_counts[name] else 0.0
        for name in perf_totals
    }
    win_totals = {name: sum(wins[name]) for name in players}
    win_totals[team] = sum(team_week_wins)
    win_counts = {name: sum(matches[name]) for name in players}
    win_counts[team] = sum(team_week_matches)
    win_avg = {
        name: round(100.0 * win_totals[name] / win_counts[name], 1) if win_counts[name] else 0.0
        for name in win_totals
    }
    player_order = sorted(players, key=lambda n: (perf_avg.get(n, 0), n), reverse=True)
    return {
        "season": season,
        "league": league,
        "team": team,
        "weeks": [f"Week {w}" for w in weeks],
        "week_numbers": weeks,
        "players": players,
        "player_order_by_average": player_order,
        "performance_data": _series(perf_data, totals=perf_totals, averages=perf_avg, counts=perf_counts),
        "win_percentage_data": _series(win_data, totals=win_totals, averages=win_avg, counts=win_counts),
    }
