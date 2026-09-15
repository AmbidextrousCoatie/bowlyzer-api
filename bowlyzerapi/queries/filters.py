from __future__ import annotations

from typing import Protocol

from bowlyzerapi.engine import Column, Expr, coalesce, lower, trim


class _EventTable(Protocol):
    season: Column
    event: Column


class _ScoreTable(Protocol):
    score: Column
    handicap: Column


def event_scope(table: _EventTable, season: str, event: str) -> Expr:
    return (table.season == season) & (table.event == event)


def is_bye(player_name: Column) -> Expr:
    folded = lower(trim(player_name))
    return (
        player_name.is_null()
        | (trim(player_name) == "")
        | folded.in_("bye", "freilos", "tbd")
        | lower(player_name).like("%(no show)%")
        | lower(player_name).like("%nicht angetreten%")
    )


def pins(table: _ScoreTable, use_net: bool) -> Expr:
    if use_net:
        return table.score + coalesce(table.handicap, 0)
    return table.score
