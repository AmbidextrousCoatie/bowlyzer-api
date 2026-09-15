"""Warehouse tables as typed column catalogs. Unknown attributes fail at lookup."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine.expr import Column, quote_ident


class Table:
    __tablename__: str

    def __init__(self, alias: str | None = None) -> None:
        if alias is not None:
            quote_ident(alias)
        self._alias = alias
        names = tuple(
            name
            for name in getattr(type(self), "__annotations__", {})
            if not name.startswith("_")
        )
        object.__setattr__(self, "_column_names", names)
        qual = alias or type(self).__tablename__
        for name in names:
            object.__setattr__(self, name, Column(name, table=qual))

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_") or name in {"as_"}:
            object.__setattr__(self, name, value)
            return
        raise AttributeError(f"Cannot assign to table column {name!r}")

    def __getattr__(self, name: str) -> Column:
        known = ", ".join(self._column_names)
        raise AttributeError(
            f"{type(self).__tablename__!r} has no column {name!r}. Known: {known}"
        )

    @property
    def name(self) -> str:
        return type(self).__tablename__

    @property
    def alias(self) -> str | None:
        return self._alias

    @property
    def qualifier(self) -> str:
        return self._alias or self.name

    def as_(self, alias: str) -> Table:
        return type(self)(alias=alias)

    def compile_from(self, params: list[Any]) -> str:
        table = quote_ident(self.name)
        if self._alias:
            return f"{table} AS {quote_ident(self._alias)}"
        return table


def relation(name: str, *columns: str) -> Table:
    """Ad-hoc CTE / subquery shape so aliases are still attribute-checked."""
    quote_ident(name)
    for col in columns:
        quote_ident(col)
    annotations = {col: Column for col in columns}
    cls = type(f"Rel_{name}", (Table,), {"__tablename__": name, "__annotations__": annotations})
    return cls()


class GameLine(Table):
    __tablename__ = "game_line"
    season: Column
    week: Column
    game_date: Column
    players_per_team: Column
    location: Column
    round_number: Column
    match_number: Column
    team: Column
    position: Column
    player_name: Column
    player_id: Column
    opponent: Column
    score: Column
    points: Column
    bonus_points: Column
    input_data: Column
    computed_data: Column
    event: Column
    event_type: Column
    club: Column


class TournamentLine(Table):
    __tablename__ = "tournament_line"
    season: Column
    game_date: Column
    location: Column
    event_type: Column
    round_number: Column
    round_name: Column
    player_name: Column
    player_id: Column
    club: Column
    game_number: Column
    score: Column
    handicap: Column
    apriori_average: Column
    handicap_reference: Column
    cumulative_score: Column
    stage_rank: Column
    cut_line: Column
    cut_basis: Column
    overall_cumulative_score: Column
    verein: Column
    history_club: Column
    affiliation_source: Column
    event: Column
    input_data: Column
    computed_data: Column


game_line = GameLine()
tournament_line = TournamentLine()
