"""Tournament query kernel: overall leaderboard + field-progress ranks."""

from __future__ import annotations

from typing import Any

from bowlyzerapi.engine import (
    ROWS_CUMULATIVE,
    Query,
    abs_,
    any_value,
    case,
    coalesce,
    count_star,
    fetch_dicts,
    fetch_rows,
    fetch_scalar,
    max_,
    range_,
    rank,
    relation,
    round_,
    row_number,
    star,
    sum_,
    tournament_line,
    trim,
    unnest,
)
from bowlyzerapi.queries.filters import event_scope, is_bye, pins
from bowlyzerapi.warehouse import connect


def tournament_section(season: str, event: str) -> dict[str, Any]:
    """Leaderboard + field-progress ranks for one published tournament event."""
    con = connect()
    try:
        t = tournament_line
        use_net = bool(
            fetch_scalar(
                con,
                Query().from_(t)
                .select(
                    (
                        (count_star().filter_where(abs_(coalesce(t.handicap, 0)) > 0) > 0)
                        | (count_star().filter_where(t.apriori_average.is_not_null()) > 0)
                    )
                )
                .where(event_scope(t, season, event)),
            )
        )
        return {
            "season": season,
            "event": event,
            "use_net": use_net,
            "leaderboard": _leaderboard(con, season, event, use_net),
            "field_progress": _field_progress(con, season, event, use_net),
        }
    finally:
        con.close()


def _leaderboard(con, season: str, event: str, use_net: bool) -> list[dict[str, Any]]:
    t = tournament_line
    pin = pins(t, use_net)
    played = (
        Query().from_(t)
        .select(
            t.player_name,
            any_value(t.player_id).as_("player_id"),
            any_value(t.club).as_("club"),
            sum_(t.score).as_("total_scratch"),
            sum_(pin).as_("total_pins"),
            count_star().filter_where(t.score > 0).as_("games"),
        )
        .where(event_scope(t, season, event), ~is_bye(t.player_name))
        .group_by(t.player_name)
    )
    p = relation(
        "played",
        "player_name",
        "player_id",
        "club",
        "total_scratch",
        "total_pins",
        "games",
    )
    return fetch_dicts(
        con,
        Query.with_ctes(played=played)
        .from_(p)
        .select(
            rank().over(order_by=[p.total_pins.desc()]).as_("rank"),
            p.player_name,
            p.player_id,
            p.club,
            p.total_scratch,
            p.total_pins,
            p.games,
            round_(p.total_scratch / p.games, 1).as_("avg_scratch"),
            round_(p.total_pins / p.games, 1).as_("avg_pins"),
        )
        .order_by("rank"),
    )


def _field_progress(con, season: str, event: str, use_net: bool) -> dict[str, Any]:
    t = tournament_line
    scope = event_scope(t, season, event)
    pin = pins(t, use_net)

    round_len_q = (
        Query().from_(t)
        .select(t.round_number, (max_(t.game_number) + 1).as_("length"))
        .where(scope, t.round_number.is_not_null(), t.game_number.is_not_null())
        .group_by(1)
        .order_by(1)
    )
    round_lens = fetch_rows(con, round_len_q)
    if not round_lens:
        return {"labels": [], "player_rank_series": {}, "participant_count": 0, "slots": 0}

    bowled_distinct = (
        Query().from_(t)
        .select(t.player_name, t.round_number, t.game_number)
        .where(scope, t.score > 0, ~is_bye(t.player_name))
        .distinct()
    )
    per_player = relation("g", "player_name", "round_number", "game_number")
    games_rel = relation("s", "player_name", "games")
    field_max = fetch_scalar(
        con,
        Query().from_(
            Query().from_(bowled_distinct.as_("g"))
            .select(per_player.player_name, count_star().as_("games"))
            .group_by(per_player.player_name)
            .as_("s")
        ).select(coalesce(max_(games_rel.games), 0)),
    )
    schedule = sum(int(length) for _, length in round_lens)
    total_games = min(int(field_max), schedule) if field_max else 0
    if total_games <= 0:
        return {"labels": [], "player_rank_series": {}, "participant_count": 0, "slots": 0}

    r = relation("round_len", "round_number", "length").as_("r")
    u_game = relation("u", "game_number")
    slots = relation("slots", "round_number", "game_number", "slot")
    capped = relation("capped", "round_number", "game_number", "slot").as_("c")
    bowled = relation("bowled", "player_name", "round_number", "game_number", "pins").as_("b")
    players = relation("players", "player_name").as_("p")
    grid = relation("grid", "player_name", "slot", "round_number", "game_number").as_("g")
    running = relation("running", "player_name", "slot", "cum_pins", "cum_games")
    active = relation("active", "slot", "player_name", "rank")

    ranked = fetch_rows(
        con,
        Query.with_ctes(
            round_len=round_len_q,
            slots=(
                Query().from_(r, unnest(range_(r.length), alias="u", columns=("game_number",)))
                .select(
                    r.round_number,
                    u_game.game_number,
                    row_number().over(order_by=[r.round_number, u_game.game_number]).as_("slot"),
                )
            ),
            capped=Query().from_(slots).select(star()).where(slots.slot <= total_games),
            bowled=(
                Query().from_(t)
                .select(
                    trim(t.player_name).as_("player_name"),
                    t.round_number,
                    t.game_number,
                    sum_(pin).as_("pins"),
                )
                .where(scope, t.score > 0, ~is_bye(t.player_name))
                .group_by(1, 2, 3)
            ),
            players=(
                Query().from_(t)
                .select(trim(t.player_name).as_("player_name"))
                .where(scope, ~is_bye(t.player_name))
                .distinct()
            ),
            grid=(
                Query().from_(players)
                .cross_join(capped)
                .select(players.player_name, capped.slot, capped.round_number, capped.game_number)
            ),
            running=(
                Query().from_(grid)
                .left_join(
                    bowled,
                    on=(
                        (bowled.player_name == grid.player_name)
                        & (bowled.round_number == grid.round_number)
                        & (bowled.game_number == grid.game_number)
                    ),
                )
                .select(
                    grid.player_name,
                    grid.slot,
                    sum_(coalesce(bowled.pins, 0))
                    .over(
                        partition_by=[grid.player_name],
                        order_by=[grid.slot],
                        frame=ROWS_CUMULATIVE,
                    )
                    .as_("cum_pins"),
                    sum_(case((coalesce(bowled.pins, 0) > 0, 1), else_=0))
                    .over(
                        partition_by=[grid.player_name],
                        order_by=[grid.slot],
                        frame=ROWS_CUMULATIVE,
                    )
                    .as_("cum_games"),
                )
            ),
            active=(
                Query().from_(running)
                .select(
                    running.slot,
                    running.player_name,
                    running.cum_pins,
                    running.cum_games,
                    row_number()
                    .over(
                        partition_by=[running.slot],
                        order_by=[
                            running.cum_pins.desc(),
                            (running.cum_pins / running.cum_games).desc(),
                            running.player_name,
                        ],
                    )
                    .as_("rank"),
                )
                .where(running.cum_games > 0)
            ),
        )
        .from_(active)
        .select(active.slot, active.player_name, active.rank)
        .order_by(active.slot, active.rank),
    )

    names = sorted({row[1] for row in ranked})
    series: dict[str, list[int]] = {name: [] for name in names}
    by_slot: dict[int, dict[str, int]] = {}
    for slot, name, rank_value in ranked:
        by_slot.setdefault(int(slot), {})[name] = int(rank_value)
    default_rank = len(names) + 1
    for slot in range(1, total_games + 1):
        ranks = by_slot.get(slot, {})
        for name in names:
            prev = series[name][-1] if series[name] else default_rank
            series[name].append(int(ranks.get(name, prev)))

    return {
        "labels": [f"G{i}" for i in range(1, total_games + 1)],
        "player_rank_series": series,
        "participant_count": len(names),
        "slots": total_games,
        "use_net": use_net,
    }
