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
from bowlyzerapi.warehouse import connect, session


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


def tournament_list(*, season: str | None = None, club: str | None = None) -> dict[str, Any]:
    t = tournament_line
    with session() as con:
        q = Query().from_(t).select(t.season, t.event).where(t.event.is_not_null()).distinct()
        if season:
            q = q.where(t.season == season)
        if club:
            from bowlyzerapi.engine import lower

            q = q.where(lower(trim(t.club)) == lower(trim(club)))
        pairs = fetch_rows(con, q.order_by(t.season.desc(), t.event))
    items = []
    seen: set[tuple[str, str]] = set()
    for s, e in pairs:
        key = (str(s), str(e))
        if key in seen:
            continue
        seen.add(key)
        items.append({"season": key[0], "event": key[1]})
    return {"tournaments": items}


def tournament_document(season: str, event: str) -> dict[str, Any]:
    kernel = tournament_section(season, event)
    t = tournament_line
    with session() as con:
        rounds = fetch_dicts(
            con,
            Query()
            .from_(t)
            .select(t.round_number, any_value(t.round_name).as_("round_name"))
            .where(event_scope(t, season, event), t.round_number.is_not_null())
            .group_by(t.round_number)
            .order_by(t.round_number),
        )
        games = fetch_dicts(
            con,
            Query()
            .from_(t)
            .select(
                trim(t.player_name).as_("player_name"),
                t.round_number,
                t.round_name,
                t.game_number,
                t.score,
                t.handicap,
                t.club,
            )
            .where(event_scope(t, season, event), ~is_bye(t.player_name))
            .order_by(t.round_number, t.game_number, t.player_name),
        )
        hdc = fetch_scalar(
            con,
            Query()
            .from_(t)
            .select(count_star().filter_where(abs_(coalesce(t.handicap, 0)) > 0))
            .where(event_scope(t, season, event)),
        )
    by_player_round: dict[tuple[str, int], dict[str, Any]] = {}
    for row in games:
        name = str(row["player_name"])
        rn = int(row["round_number"]) if row["round_number"] is not None else 0
        slot = by_player_round.setdefault(
            (name, rn),
            {
                "player_name": name,
                "round_number": rn,
                "round_name": row["round_name"],
                "club": row["club"],
                "games": {},
                "total_scratch": 0,
            },
        )
        gn = int(row["game_number"]) if row["game_number"] is not None else 0
        score = int(row["score"] or 0)
        slot["games"][gn] = score
        slot["total_scratch"] += score
    round_results = []
    for slot in sorted(by_player_round.values(), key=lambda r: (r["round_number"], r["player_name"])):
        played = [v for v in slot["games"].values() if v]
        round_results.append(
            {
                **slot,
                "games": [slot["games"].get(i) for i in sorted(slot["games"])],
                "avg_score": round(sum(played) / len(played), 1) if played else None,
            }
        )
    leader = kernel["leaderboard"][0] if kernel["leaderboard"] else None
    cards = [
        {"title": "Tournament", "value": event},
        {"title": "Participants", "value": len(kernel["leaderboard"])},
    ]
    if leader:
        cards.append(
            {
                "title": "Leader",
                "value": leader["player_name"],
                "subtitle": f"{leader['total_pins']} pins",
            }
        )
    kernel.update(
        {
            "rounds": [{"round_number": r["round_number"], "round_name": r["round_name"]} for r in rounds],
            "cards": cards,
            "round_results": round_results,
            "format": {
                "round_count": len(rounds),
                "rounds": [{"round_number": r["round_number"], "round_name": r["round_name"]} for r in rounds],
                "handicap": {"used": bool(hdc)},
            },
            "ko_bracket": None,
        }
    )
    return kernel


def tournament_player_section(season: str, event: str, player: str) -> dict[str, Any]:
    doc = tournament_document(season, event)
    name = player.strip()
    lb = next(
        (r for r in doc["leaderboard"] if str(r["player_name"]).lower() == name.lower()),
        None,
    )
    series = doc["field_progress"].get("player_rank_series", {}).get(lb["player_name"] if lb else name, [])
    games = [r for r in doc["round_results"] if str(r["player_name"]).lower() == name.lower()]
    scores = [s for r in games for s in r["games"] if s]
    return {
        "season": season,
        "event": event,
        "player": lb["player_name"] if lb else name,
        "player_club": lb.get("club") if lb else None,
        "summary": {
            "average": lb.get("avg_pins") if lb else None,
            "final_position": lb.get("rank") if lb else None,
            "best_position": min(series) if series else None,
        },
        "round_table": games,
        "progress_series": {
            "labels": doc["field_progress"].get("labels"),
            "position_series": series,
        },
        "field_progress": doc["field_progress"],
        "ko_bracket": None,
        "best_efforts": {
            "highest_game": {"score": max(scores) if scores else None},
        },
    }


def tournament_podiums(*, season: str | None = None, club: str | None = None, n: int = 3) -> dict[str, Any]:
    listing = tournament_list(season=season, club=club)
    n = max(1, min(int(n), 10))
    podiums = []
    for item in listing["tournaments"]:
        if season and item["season"] != season:
            continue
        section = tournament_section(item["season"], item["event"])
        finishers = []
        for row in section["leaderboard"][:n]:
            if club and str(row.get("club") or "").lower() != club.lower():
                continue
            finishers.append(
                {
                    "rank": row["rank"],
                    "player": row["player_name"],
                    "club": row.get("club"),
                    "average": row.get("avg_pins"),
                }
            )
        if club and not finishers:
            continue
        podiums.append(
            {
                "season": item["season"],
                "tournament": item["event"],
                "finishers": finishers[:n],
            }
        )
    return {"top_n": n, "podiums": podiums}
