"""Club history: teams × seasons with league and table position."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine import (
    Query,
    case,
    concat,
    count_distinct,
    fetch_one,
    fetch_rows,
    game_line,
    lower,
    rank,
    regexp_extract,
    relation,
    sum_,
    trim,
    try_cast,
)
from bowlyzerapi.warehouse import connect


def club_history(club: str) -> dict[str, Any]:
    club = (club or "").strip()
    if not club:
        return {"club": "", "seasons": [], "rows": []}

    con = connect()
    try:
        canonical = _resolve_club(con, club)
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


def _resolve_club(con, club: str) -> str | None:
    g = game_line
    exact = fetch_one(
        con,
        Query().from_(g)
        .select(g.club)
        .where(
            g.club.is_not_null(),
            trim(g.club) != "",
            lower(trim(g.club)) == lower(trim(club)),
        )
        .limit(1),
    )
    if exact:
        return str(exact[0])
    fuzzy = fetch_one(
        con,
        Query().from_(g)
        .select(g.club)
        .where(g.club.is_not_null(), g.club.ilike(concat("%", club, "%")))
        .limit(1),
    )
    return str(fuzzy[0]) if fuzzy else None


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
