from __future__ import annotations

from typing import Protocol

from bowlyzerapi.engine import Column, Expr, Table, coalesce, concat, lower, trim
from bowlyzerapi.engine.query import Query, fetch_one
from bowlyzerapi.engine.schema import game_line


class _EventTable(Protocol):
    season: Column
    event: Column


class _ScoreTable(Protocol):
    score: Column
    handicap: Column


def event_scope(table: _EventTable, season: str, event: str) -> Expr:
    return (table.season == season) & (table.event == event)


def is_league_fact(table: Table) -> Expr:
    return table.event_type.is_null() | (table.event_type == "league") | (table.event_type == "")


def is_player_game(table: Table) -> Expr:
    return table.computed_data.is_false()


def is_team_total(table: Table) -> Expr:
    return table.computed_data.is_true()


def is_bye(player_name: Column) -> Expr:
    folded = lower(trim(player_name))
    return (
        player_name.is_null()
        | (trim(player_name) == "")
        | folded.in_("bye", "freilos", "tbd", "team total")
        | lower(player_name).like("%(no show)%")
        | lower(player_name).like("%nicht angetreten%")
    )


def pins(table: _ScoreTable, use_net: bool) -> Expr:
    if use_net:
        return table.score + coalesce(table.handicap, 0)
    return table.score


def resolve_club(con, club: str) -> str | None:
    g = game_line
    club = (club or "").strip()
    if not club:
        return None
    exact = fetch_one(
        con,
        Query()
        .from_(g)
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
        Query()
        .from_(g)
        .select(g.club)
        .where(g.club.is_not_null(), g.club.ilike(concat("%", club, "%")))
        .limit(1),
    )
    return str(fuzzy[0]) if fuzzy else None
