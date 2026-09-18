"""Tournament query kernel: leaderboard, field progress, document, player section."""

from __future__ import annotations

import math
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
    lower,
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
from bowlyzerapi.queries.identity import apply_canonical_player_names, collapse_player_catalog
from bowlyzerapi.queries.tournament_ko import (
    apply_ko_ranks,
    build_ko_bracket,
    format_ko_fields,
    is_ko_round_name,
    ko_finale_round_number,
    placement_for,
    player_in_bracket,
    with_highlights,
)
from bowlyzerapi.queries.tournament_names import normalize_tournament_group_name
from bowlyzerapi.queries.util import as_float, as_int, as_str
from bowlyzerapi.warehouse import connect, session

_PLAYER_CARD_ORDER = (
    "summary_final_position",
    "summary_average",
    "summary_best_position",
    "best_highest_game",
    "best_highest_pair",
    "handicap_profile",
    "best_highest_block",
)


def tournament_section(season: str, event: str) -> dict[str, Any]:
    """Leaderboard + field-progress ranks for one published tournament event (or group)."""
    con = connect()
    try:
        events = _resolve_events(con, season, event)
        if not events:
            return {
                "season": season,
                "event": event,
                "use_net": False,
                "leaderboard": [],
                "field_progress": {
                    "labels": [],
                    "player_rank_series": {},
                    "participant_count": 0,
                    "slots": 0,
                },
            }
        t = tournament_line
        use_net = bool(
            fetch_scalar(
                con,
                Query()
                .from_(t)
                .select(
                    (
                        (count_star().filter_where(abs_(coalesce(t.handicap, 0)) > 0) > 0)
                        | (count_star().filter_where(t.apriori_average.is_not_null()) > 0)
                    )
                )
                .where(_event_filter(t, season, events)),
            )
        )
        return {
            "season": season,
            "event": event,
            "use_net": use_net,
            "leaderboard": _leaderboard(con, season, events, use_net),
            "field_progress": _field_progress(con, season, events, use_net),
        }
    finally:
        con.close()


def _event_filter(table, season: str, events: list[str]):
    if not events:
        return (table.season == season) & (table.event == "")
    if len(events) == 1:
        return event_scope(table, season, events[0])
    return (table.season == season) & table.event.in_(*events)


def _resolve_events(con, season: str | None, event: str | None) -> list[str]:
    needle = (event or "").strip()
    if not needle:
        return []
    t = tournament_line
    q = Query().from_(t).select(t.event).where(t.event.is_not_null()).distinct()
    if season:
        q = q.where(t.season == season)
    names = [str(row[0]).strip() for row in fetch_rows(con, q) if row[0] and str(row[0]).strip()]
    exact = [name for name in names if name == needle]
    if exact:
        return exact
    group = normalize_tournament_group_name(needle)
    return [name for name in names if normalize_tournament_group_name(name) == group]


def _leaderboard(con, season: str, events: list[str], use_net: bool) -> list[dict[str, Any]]:
    t = tournament_line
    pin = pins(t, use_net)
    key = coalesce(t.player_id, trim(t.player_name))
    played = (
        Query()
        .from_(t)
        .select(
            key.as_("player_key"),
            any_value(t.player_name).as_("player_name"),
            any_value(t.player_id).as_("player_id"),
            any_value(t.club).as_("club"),
            sum_(t.score).as_("total_scratch"),
            sum_(pin).as_("total_pins"),
            count_star().filter_where(t.score > 0).as_("games"),
        )
        .where(_event_filter(t, season, events), ~is_bye(t.player_name))
        .group_by(key)
    )
    p = relation(
        "played",
        "player_key",
        "player_name",
        "player_id",
        "club",
        "total_scratch",
        "total_pins",
        "games",
    )
    rows = fetch_dicts(
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
    apply_canonical_player_names(con, rows)
    return rows


def _field_progress(con, season: str, events: list[str], use_net: bool) -> dict[str, Any]:
    t = tournament_line
    scope = _event_filter(t, season, events)
    pin = pins(t, use_net)

    round_len_q = (
        Query()
        .from_(t)
        .select(t.round_number, (max_(t.game_number) + 1).as_("length"))
        .where(scope, t.round_number.is_not_null(), t.game_number.is_not_null())
        .group_by(1)
        .order_by(1)
    )
    round_lens = fetch_rows(con, round_len_q)
    empty = {
        "labels": [],
        "player_rank_series": {},
        "participant_count": 0,
        "slots": 0,
        "tournament_leader_avg_series": [],
        "tournament_lowest_avg_series": [],
        "round_end_lines": [],
        "game_slots": [],
        "round_length_map": {},
    }
    if not round_lens:
        return empty

    bowled_distinct = (
        Query()
        .from_(t)
        .select(t.player_name, t.round_number, t.game_number)
        .where(scope, t.score > 0, ~is_bye(t.player_name))
        .distinct()
    )
    per_player = relation("g", "player_name", "round_number", "game_number")
    games_rel = relation("s", "player_name", "games")
    field_max = fetch_scalar(
        con,
        Query()
        .from_(
            Query()
            .from_(bowled_distinct.as_("g"))
            .select(per_player.player_name, count_star().as_("games"))
            .group_by(per_player.player_name)
            .as_("s")
        )
        .select(coalesce(max_(games_rel.games), 0)),
    )
    schedule = sum(int(length) for _, length in round_lens)
    total_games = min(int(field_max), schedule) if field_max else 0
    if total_games <= 0:
        return empty

    r = relation("round_len", "round_number", "length").as_("r")
    u_game = relation("u", "game_number")
    slots = relation("slots", "round_number", "game_number", "slot")
    capped = relation("capped", "round_number", "game_number", "slot").as_("c")
    bowled = relation("bowled", "player_name", "round_number", "game_number", "pins").as_("b")
    players = relation("players", "player_name").as_("p")
    grid = relation("grid", "player_name", "slot", "round_number", "game_number").as_("g")
    running = relation("running", "player_name", "slot", "cum_pins", "cum_games")
    active = relation("active", "slot", "player_name", "cum_pins", "cum_games", "rank")

    ranked = fetch_rows(
        con,
        Query.with_ctes(
            round_len=round_len_q,
            slots=(
                Query()
                .from_(r, unnest(range_(r.length), alias="u", columns=("game_number",)))
                .select(
                    r.round_number,
                    u_game.game_number,
                    row_number().over(order_by=[r.round_number, u_game.game_number]).as_("slot"),
                )
            ),
            capped=Query().from_(slots).select(star()).where(slots.slot <= total_games),
            bowled=(
                Query()
                .from_(t)
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
                Query()
                .from_(t)
                .select(trim(t.player_name).as_("player_name"))
                .where(scope, ~is_bye(t.player_name))
                .distinct()
            ),
            grid=(
                Query()
                .from_(players)
                .cross_join(capped)
                .select(players.player_name, capped.slot, capped.round_number, capped.game_number)
            ),
            running=(
                Query()
                .from_(grid)
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
                Query()
                .from_(running)
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
        .select(active.slot, active.player_name, active.rank, active.cum_pins, active.cum_games)
        .order_by(active.slot, active.rank),
    )

    names = sorted({row[1] for row in ranked})
    series: dict[str, list[int]] = {name: [] for name in names}
    by_slot: dict[int, dict[str, int]] = {}
    avgs_by_slot: dict[int, list[float]] = {}
    for slot, name, rank_value, cum_pins, cum_games in ranked:
        slot_i = int(slot)
        by_slot.setdefault(slot_i, {})[name] = int(rank_value)
        games_n = float(cum_games or 0)
        if games_n > 0:
            avgs_by_slot.setdefault(slot_i, []).append(float(cum_pins or 0) / games_n)
    default_rank = len(names) + 1
    leader_avg: list[float | None] = []
    lowest_avg: list[float | None] = []
    for slot in range(1, total_games + 1):
        ranks = by_slot.get(slot, {})
        for name in names:
            prev = series[name][-1] if series[name] else default_rank
            series[name].append(int(ranks.get(name, prev)))
        slot_avgs = avgs_by_slot.get(slot) or []
        if slot_avgs:
            leader_avg.append(round(max(slot_avgs), 2))
            lowest_avg.append(round(min(slot_avgs), 2))
        else:
            leader_avg.append(None)
            lowest_avg.append(None)

    game_slots: list[list[int]] = []
    round_length_map: dict[str, int] = {}
    round_end_lines: list[int] = []
    acc = 0
    for rn, length in round_lens:
        length_i = int(length)
        round_length_map[str(int(rn))] = length_i
        for game_number in range(length_i):
            if acc >= total_games:
                break
            acc += 1
            game_slots.append([int(rn), game_number])
            if game_number == length_i - 1:
                round_end_lines.append(acc)

    return {
        "labels": [f"G{i}" for i in range(1, total_games + 1)],
        "player_rank_series": series,
        "participant_count": len(names),
        "slots": total_games,
        "use_net": use_net,
        "tournament_leader_avg_series": leader_avg,
        "tournament_lowest_avg_series": lowest_avg,
        "round_end_lines": round_end_lines,
        "game_slots": game_slots,
        "round_length_map": round_length_map,
    }


def tournament_list(
    *,
    season: str | None = None,
    club: str | None = None,
    event: str | None = None,
) -> dict[str, Any]:
    t = tournament_line
    with session() as con:
        q = Query().from_(t).select(t.season, t.event).where(t.event.is_not_null()).distinct()
        if season:
            q = q.where(t.season == season)
        if club:
            q = q.where(lower(trim(t.club)) == lower(trim(club)))
        if event:
            names = _resolve_events(con, season, event)
            if names:
                q = q.where(t.event.in_(*names) if len(names) > 1 else t.event == names[0])
            else:
                q = q.where(t.event == event)
        pairs = fetch_rows(con, q.order_by(t.season.desc(), t.event))
    items = []
    seen: set[tuple[str, str]] = set()
    groups: set[str] = set()
    seasons: set[str] = set()
    for s, e in pairs:
        key = (str(s), str(e))
        if key in seen:
            continue
        seen.add(key)
        group = normalize_tournament_group_name(key[1])
        groups.add(group or key[1])
        seasons.add(key[0])
        items.append({"season": key[0], "event": key[1], "tournament_group": group or key[1]})
    return {
        "tournaments": items,
        "seasons": sorted(seasons, reverse=True),
        "events": sorted(groups),
    }


def tournament_document(
    season: str,
    event: str,
    *,
    round: int | None = None,
    n: int = 5,
) -> dict[str, Any]:
    kernel = tournament_section(season, event)
    t = tournament_line
    with session() as con:
        events = _resolve_events(con, season, event)
        if not events:
            kernel.update(
                {
                    "rounds": [],
                    "cards": [{"title": "Tournament", "value": event}],
                    "round_results": [],
                    "best_efforts": {"n": n, "sections": []},
                    "players": [],
                    "format": _empty_format(),
                    "ko_bracket": None,
                    "is_ko_finale_round": False,
                    "ko_finale_round_number": None,
                    "round": round,
                }
            )
            return kernel
        games = fetch_dicts(
            con,
            Query()
            .from_(t)
            .select(
                trim(t.player_name).as_("player_name"),
                t.player_id,
                t.club,
                t.round_number,
                t.round_name,
                t.game_number,
                t.score,
                t.handicap,
                t.apriori_average,
                t.handicap_reference,
                t.stage_rank,
            )
            .where(_event_filter(t, season, events), ~is_bye(t.player_name))
            .order_by(t.round_number, t.game_number, t.player_name),
        )
        orig_names = [(as_str(row.get("player_id")), as_str(row.get("player_name"))) for row in games]
        apply_canonical_player_names(con, games)
    ko_bracket = build_ko_bracket(season, event, games)
    ko_rn = ko_finale_round_number(games)
    scored = [row for row in games if not _is_walkover(row)]
    kernel["field_progress"] = _remap_field_progress(kernel.get("field_progress") or {}, orig_names, scored)
    rounds = _rounds_from_games(scored)
    use_net = bool(kernel.get("use_net"))
    top_n = max(1, min(int(n or 5), 20))
    leaderboard = _leaderboard_from_games(scored, use_net=use_net, through_round=round)
    if round is None and ko_bracket.get("matches"):
        leaderboard = apply_ko_ranks(leaderboard, ko_bracket)
    round_results = _round_results_from_games(scored, use_net=use_net, round_number=round)
    cards = _summary_cards(event, scored, leaderboard, rounds, round_number=round, use_net=use_net)
    is_ko_finale = (
        round is not None and ko_rn is not None and int(round) == int(ko_rn) and bool(ko_bracket.get("matches"))
    )
    kernel.update(
        {
            "leaderboard": leaderboard,
            "rounds": rounds,
            "cards": cards,
            "round_results": round_results,
            "best_efforts": _best_efforts(scored, round_number=round, n=top_n),
            "players": _players_from_games(scored, round_number=round),
            "format": _format_info(scored, rounds, use_net=use_net, season=season, event=event),
            "ko_bracket": ko_bracket if ko_bracket.get("matches") else None,
            "is_ko_finale_round": is_ko_finale,
            "ko_finale_round_number": ko_rn,
            "round": round,
        }
    )
    return kernel


def tournament_player_section(season: str, event: str, player: str) -> dict[str, Any]:
    doc = tournament_document(season, event)
    needle = player.strip()
    folded = needle.casefold()
    lb = next(
        (
            row
            for row in doc["leaderboard"]
            if str(row.get("player") or row.get("player_name") or "").casefold() == folded
            or str(row.get("player_id") or "") == needle
        ),
        None,
    )
    display = str((lb or {}).get("player") or (lb or {}).get("player_name") or needle)
    field = doc.get("field_progress") or {}
    rank_map = field.get("player_rank_series") or {}
    series = list(rank_map.get(display) or rank_map.get(needle) or [])
    if not series:
        for name, values in rank_map.items():
            if str(name).casefold() == folded:
                series = list(values)
                display = name
                break
    t = tournament_line
    with session() as con:
        events = _resolve_events(con, season, event)
        games = []
        if events:
            games = fetch_dicts(
                con,
                Query()
                .from_(t)
                .select(
                    trim(t.player_name).as_("player_name"),
                    t.player_id,
                    t.club,
                    t.round_number,
                    t.round_name,
                    t.game_number,
                    t.score,
                    t.handicap,
                    t.apriori_average,
                    t.handicap_reference,
                )
                .where(_event_filter(t, season, events), ~is_bye(t.player_name))
                .order_by(t.round_number, t.game_number),
            )
            apply_canonical_player_names(con, games)
    player_games = [
        row
        for row in games
        if not _is_walkover(row)
        and (
            str(row.get("player_name") or "").casefold() in {folded, display.casefold()}
            or str(row.get("player_id") or "") == needle
            or str(row.get("player_id") or "") == str((lb or {}).get("player_id") or "")
        )
    ]
    pkey = _player_key(player_games[0]) if player_games else (_player_key(lb) if lb else needle)
    use_net = bool(doc.get("use_net"))
    round_table = _player_round_table_from_games(games, pkey, use_net=use_net)
    progress = _player_progress(field, player_games)
    if series:
        progress["position_series"] = series
    played_avgs = [v for v in (progress.get("avg_series") or []) if v is not None]
    best_position = min(series) if series else None
    best_game = None
    if best_position is not None and series:
        labels = field.get("labels") or progress.get("labels") or []
        try:
            idx = series.index(best_position)
            best_game = labels[idx] if idx < len(labels) else None
        except ValueError:
            best_game = None
    want_pairs = _want_pairs(event)
    fmt = doc.get("format") or {}
    cfg_cards = (fmt.get("config") or {}).get("player_cards") if isinstance(fmt.get("config"), dict) else None
    layout_src = cfg_cards if isinstance(cfg_cards, list) and cfg_cards else _PLAYER_CARD_ORDER
    layout = [
        cid
        for cid in layout_src
        if not (cid == "best_highest_pair" and not want_pairs)
        and not (cid == "handicap_profile" and not use_net)
    ]
    ko_raw = doc.get("ko_bracket") if isinstance(doc.get("ko_bracket"), dict) else None
    ko_place = placement_for(ko_raw, display)
    if ko_place is not None and series and not field.get("progress_chart_capped"):
        series = series[:]
        series[-1] = ko_place
        progress["position_series"] = series
        if best_position is None or ko_place < best_position:
            best_position = min(series)
    if ko_raw and player_in_bracket(ko_raw, display):
        ko_for_player = with_highlights(ko_raw, display)
    else:
        ko_for_player = None
    return {
        "season": season,
        "event": event,
        "player": display,
        "player_club": (lb or {}).get("club"),
        "player_card_layout": layout or list(_PLAYER_CARD_ORDER[:4]) + ["best_highest_block"],
        "summary": {
            "average": round(float(played_avgs[-1]), 2) if played_avgs else (lb or {}).get("avg_net") or (lb or {}).get("avg_scratch"),
            "final_position": ko_place if ko_place is not None else (lb or {}).get("rank"),
            "best_position": best_position,
            "best_position_game": best_game,
        },
        "round_table": round_table,
        "progress_series": {
            "labels": field.get("labels") or [],
            **progress,
        },
        "field_progress": {
            k: v
            for k, v in field.items()
            if k not in {"player_rank_series", "game_slots", "round_length_map"}
        },
        "ko_bracket": ko_for_player,
        "best_efforts": _player_best_efforts(player_games, use_net=use_net),
    }


def tournament_podiums(
    *,
    season: str | None = None,
    club: str | None = None,
    event: str | None = None,
    n: int = 3,
) -> dict[str, Any]:
    listing = tournament_list(season=season, club=club, event=event)
    n = max(1, min(int(n), 10))
    group_filter = normalize_tournament_group_name(event) if event else ""
    seen_groups: set[tuple[str, str]] = set()
    podiums = []
    for item in listing["tournaments"]:
        group = item.get("tournament_group") or normalize_tournament_group_name(item["event"])
        if group_filter and group != group_filter:
            continue
        key = (item["season"], group)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        section = tournament_section(item["season"], group)
        finishers = []
        for row in section["leaderboard"][:n]:
            if club and str(row.get("club") or "").casefold() != club.casefold():
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
                "tournament": group,
                "tournament_group": group,
                "finishers": finishers[:n],
            }
        )
    return {"top_n": n, "podiums": podiums}


def tournament_players(
    *,
    season: str | None = None,
    event: str | None = None,
    round: int | None = None,
) -> dict[str, Any]:
    t = tournament_line
    with session() as con:
        q = (
            Query()
            .from_(t)
            .select(
                any_value(t.player_id).as_("player_id"),
                trim(t.player_name).as_("player_name"),
                count_star().as_("n"),
            )
            .where(~is_bye(t.player_name), t.player_name.is_not_null())
            .group_by(trim(t.player_name), t.player_id)
        )
        if season:
            q = q.where(t.season == season)
        if event:
            names = _resolve_events(con, season, event)
            if names:
                q = q.where(t.event.in_(*names) if len(names) > 1 else t.event == names[0])
            else:
                q = q.where(t.event == event)
        if round is not None:
            q = q.where(t.round_number == round)
        rows = fetch_dicts(con, q)
        catalog = collapse_player_catalog(
            [{"player_id": row.get("player_id"), "player_name": row.get("player_name"), "n": row.get("n")} for row in rows]
        )
    players = sorted(
        [{"id": row.get("id") or "", "name": row["name"], "aliases": row.get("aliases") or []} for row in catalog if row.get("name")],
        key=lambda row: str(row["name"]).casefold(),
    )
    return {"players": players}


def _empty_format() -> dict[str, Any]:
    return {
        "round_count": 0,
        "rounds": [],
        "handicap": {
            "used": False,
            "columns": {"handicap": False, "apriori_average": False, "handicap_reference": False},
            "pins": None,
            "a_priori_average": None,
            "handicap_reference": None,
        },
        "ko_finale_round_number_in_data": None,
        "qualifying_cut_span": None,
        "qualifying_cut_pair": None,
        "qualifying_stages": [],
        "config": {},
    }


def _num(value: Any, default: float = 0.0) -> float:
    n = as_float(value)
    return default if n is None else n


def _arith_round_int(value: float) -> int:
    if not math.isfinite(value):
        return 0
    if value >= 0:
        return math.floor(value + 0.5)
    return -math.floor(-value + 0.5)


def _player_key(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    pid = as_str(row.get("player_id")) or ""
    if pid and pid.casefold() not in {"0", "nan", "none"}:
        return pid
    return as_str(row.get("player_name") or row.get("player")) or ""


def _is_walkover(row: dict[str, Any]) -> bool:
    return (as_str(row.get("club")) or "") == "KO_WO"


def _want_pairs(event: str) -> bool:
    folded = event.casefold()
    return any(token in folded for token in ("nbm", "sbm", "nordbay", "südbay", "suedbay", "sudbay"))


def _handicap_label(values: list[float]) -> Any:
    if not values:
        return "—"
    uniq = {round(v, 4) for v in values}
    if len(uniq) == 1:
        return _arith_round_int(next(iter(uniq)))
    return f"{_arith_round_int(min(values))}-{_arith_round_int(max(values))}"


def _band_int(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    vmin, vmax = min(values), max(values)
    if abs(vmax - vmin) < 0.5:
        return {"kind": "uniform", "value": _arith_round_int((vmin + vmax) / 2)}
    return {
        "kind": "range",
        "min": _arith_round_int(vmin),
        "max": _arith_round_int(vmax),
        "mean": round(sum(values) / len(values), 1),
    }


def _band_float(values: list[float], decimals: int = 1) -> dict[str, Any] | None:
    if not values:
        return None
    vmin, vmax = min(values), max(values)
    tol = 10 ** (-decimals)
    if abs(vmax - vmin) < tol:
        return {"kind": "uniform", "value": round((vmin + vmax) / 2.0, decimals)}
    return {
        "kind": "range",
        "min": round(vmin, decimals),
        "max": round(vmax, decimals),
        "mean": round(sum(values) / len(values), decimals),
    }


def _rounds_from_games(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_rn: dict[int, str] = {}
    for row in games:
        rn = as_int(row.get("round_number"))
        if rn is None:
            continue
        name = as_str(row.get("round_name")) or f"Round {rn}"
        by_rn.setdefault(rn, name)
    return [
        {
            "round_number": rn,
            "round_name": by_rn[rn],
            "is_ko_finale_cluster": is_ko_round_name(by_rn[rn]),
        }
        for rn in sorted(by_rn)
    ]


def _min_rank(values: list[float]) -> list[int]:
    order = sorted(range(len(values)), key=lambda i: (-values[i], i))
    ranks = [0] * len(values)
    rank_n = 1
    for i, idx in enumerate(order):
        if i > 0 and values[idx] < values[order[i - 1]]:
            rank_n = i + 1
        ranks[idx] = rank_n
    return ranks


def _aggregate_player(games: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    players: dict[str, dict[str, Any]] = {}
    for row in games:
        key = _player_key(row)
        if not key:
            continue
        bucket = players.setdefault(
            key,
            {
                "player_id": as_str(row.get("player_id")),
                "player": as_str(row.get("player_name")) or "",
                "club": as_str(row.get("club")) or "",
                "games": 0,
                "total_score": 0,
                "total_net": 0.0,
                "handicap": [],
                "rounds": {},
            },
        )
        score = _num(row.get("score"))
        hcp = _num(row.get("handicap"))
        if score > 0:
            bucket["games"] += 1
        bucket["total_score"] += int(score)
        bucket["total_net"] += score + hcp
        if row.get("handicap") is not None:
            bucket["handicap"].append(hcp)
        rn = as_int(row.get("round_number"))
        if rn is None:
            continue
        round_bucket = bucket["rounds"].setdefault(rn, {"scratch": 0, "net": 0.0, "games": 0, "name": as_str(row.get("round_name"))})
        round_bucket["scratch"] += int(score)
        round_bucket["net"] += score + hcp
        if score > 0:
            round_bucket["games"] += 1
    return players


def _leaderboard_from_games(
    games: list[dict[str, Any]],
    *,
    use_net: bool,
    through_round: int | None,
) -> list[dict[str, Any]]:
    scoped = games
    if through_round is not None:
        scoped = [row for row in games if as_int(row.get("round_number")) is not None and as_int(row["round_number"]) <= through_round]
    selected = (
        [row for row in games if as_int(row.get("round_number")) == through_round]
        if through_round is not None
        else games
    )
    totals = _aggregate_player(scoped)
    stage = _aggregate_player(selected) if through_round is not None else {}
    round_numbers = sorted(
        {
            rn
            for row in scoped
            if (rn := as_int(row.get("round_number"))) is not None
        }
    )
    keys = list(stage) if through_round is not None else list(totals)
    rows = []
    for key in keys:
        bucket = totals.get(key) or stage.get(key)
        if not bucket:
            continue
        games_n = max(int(bucket["games"]), 1)
        row: dict[str, Any] = {
            "player_id": bucket["player_id"],
            "player": bucket["player"],
            "player_name": bucket["player"],
            "club": bucket["club"],
            "games": bucket["games"],
            "total_score": int(bucket["total_score"]),
            "avg_scratch": round(bucket["total_score"] / games_n, 1),
            "total_avg": round(bucket["total_score"] / games_n, 1),
        }
        if use_net:
            row["total_net"] = _arith_round_int(bucket["total_net"])
            row["avg_net"] = round(bucket["total_net"] / games_n, 1)
            row["total_avg_net"] = row["avg_net"]
            row["handicap_display"] = _handicap_label(bucket["handicap"])
        if through_round is not None:
            stage_bucket = stage.get(key) or {"scratch": 0, "net": 0.0, "games": 0}
            stage_games = max(int(stage_bucket["games"] or stage_bucket.get("games", 0)), 1)
            if isinstance(stage_bucket, dict) and "rounds" in stage_bucket:
                rnd = (stage_bucket.get("rounds") or {}).get(through_round) or {"scratch": 0, "net": 0.0, "games": 0}
                row["round_score"] = int(rnd["scratch"])
                row["avg_score"] = round(rnd["scratch"] / max(int(rnd["games"]), 1), 1)
                if use_net:
                    row["round_net"] = _arith_round_int(rnd["net"])
                    row["avg_round_net"] = round(rnd["net"] / max(int(rnd["games"]), 1), 1)
            else:
                row["round_score"] = int(stage_bucket.get("scratch") or stage_bucket.get("total_score") or 0)
        else:
            for rn in round_numbers:
                rnd = bucket["rounds"].get(rn) or {"scratch": 0}
                row[f"round_{rn}"] = int(rnd["scratch"])
        rows.append(row)
    metric = "total_net" if use_net else "total_score"
    values = [float(row.get(metric) or 0) for row in rows]
    ranks = _min_rank(values)
    for row, rank_n in zip(rows, ranks):
        row["rank"] = rank_n
        row["total_pins"] = row.get("total_net") if use_net else row["total_score"]
        row["avg_pins"] = row.get("avg_net") if use_net else row["avg_scratch"]
    rows.sort(key=lambda r: (r["rank"], str(r["player"])))
    return rows


def _round_results_from_games(
    games: list[dict[str, Any]],
    *,
    use_net: bool,
    round_number: int | None,
) -> list[dict[str, Any]]:
    work = games if round_number is None else [row for row in games if as_int(row.get("round_number")) == round_number]
    upto = games if round_number is None else [row for row in games if as_int(row.get("round_number")) is not None and as_int(row["round_number"]) <= round_number]
    overall = _aggregate_player(upto)
    by_slot: dict[tuple[str, int], dict[str, Any]] = {}
    game_numbers: set[int] = set()
    for row in work:
        key = _player_key(row)
        rn = as_int(row.get("round_number"))
        gn = as_int(row.get("game_number"))
        if not key or rn is None or gn is None:
            continue
        game_numbers.add(gn)
        slot = by_slot.setdefault(
            (key, rn),
            {
                "player_id": as_str(row.get("player_id")),
                "player": as_str(row.get("player_name")) or "",
                "player_name": as_str(row.get("player_name")) or "",
                "club": as_str(row.get("club")) or "",
                "round_number": rn,
                "round_name": as_str(row.get("round_name")) or f"Round {rn}",
                "stage": as_str(row.get("round_name")) or f"Round {rn}",
                "games": {},
                "handicap": [],
            },
        )
        score = int(_num(row.get("score")))
        hcp = _num(row.get("handicap"))
        slot["games"][gn] = score
        slot["handicap"].append(hcp)
        slot[f"game_{gn}"] = score
    ranked_keys = list(by_slot)
    stage_values = []
    rows = []
    for key, rn in ranked_keys:
        slot = by_slot[(key, rn)]
        played = [v for v in slot["games"].values() if v]
        stage_score = sum(slot["games"].values())
        stage_net = stage_score + sum(slot["handicap"])
        games_n = max(len(played), 1)
        overall_bucket = overall.get(key) or {"total_score": 0, "total_net": 0.0, "games": 1}
        row = {
            **{k: v for k, v in slot.items() if k not in {"games", "handicap"}},
            "stage_score": int(stage_score),
            "stage_avg": round(stage_score / games_n, 1),
            "round_total": int(stage_score),
            "avg_score": round(stage_score / games_n, 1),
            "overall_score": int(overall_bucket["total_score"]),
            "total_score": int(overall_bucket["total_score"]),
            "overall_avg": round(overall_bucket["total_score"] / max(int(overall_bucket["games"]), 1), 1),
            "total_avg": round(overall_bucket["total_score"] / max(int(overall_bucket["games"]), 1), 1),
            "overall_games": overall_bucket["games"],
        }
        if use_net:
            row["stage_net"] = _arith_round_int(stage_net)
            row["stage_avg_net"] = round(stage_net / games_n, 1)
            row["overall_net"] = _arith_round_int(overall_bucket["total_net"])
            row["overall_avg_net"] = round(overall_bucket["total_net"] / max(int(overall_bucket["games"]), 1), 1)
            row["handicap_display"] = _handicap_label(slot["handicap"])
        rows.append(row)
        stage_values.append(float(row["overall_net"] if use_net else row["overall_score"]))
    ranks = _min_rank(stage_values)
    for row, rank_n in zip(rows, ranks):
        row["overall_rank"] = rank_n
        row["rank"] = rank_n
    rows.sort(key=lambda r: (r["round_number"], r["overall_rank"], r["player"]))
    return rows


def _best_efforts(games: list[dict[str, Any]], *, round_number: int | None, n: int) -> dict[str, Any]:
    rounds = _rounds_from_games(games)
    if round_number is not None:
        name = next((r["round_name"] for r in rounds if r["round_number"] == round_number), f"Round {round_number}")
        scoped = [row for row in games if as_int(row.get("round_number")) == round_number]
        return {"n": n, "sections": [_best_effort_scope(scoped, name, n)]}
    sections = []
    for item in rounds:
        scoped = [row for row in games if as_int(row.get("round_number")) == item["round_number"]]
        sections.append(_best_effort_scope(scoped, item["round_name"], n))
    sections.append(_best_effort_scope(games, "Overall", n))
    return {"n": n, "sections": sections}


def _best_effort_scope(games: list[dict[str, Any]], scope: str, n: int) -> dict[str, Any]:
    return {
        "scope": scope,
        "best_games": _top_games(games, n),
        "best_pairs": _top_pairs(games, n),
        "best_blocks": _top_blocks(games, n),
    }


def _effort_row(row: dict[str, Any], *, label: str, value: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "player": as_str(row.get("player_name")) or "",
        "club": as_str(row.get("club")) or "",
        "stage": as_str(row.get("round_name")) or "",
        "label": label,
        "value": value,
        "display_value": str(value),
        **(extra or {}),
    }
    return out


def _top_games(games: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    scored = [row for row in games if as_int(row.get("score")) is not None]
    scored.sort(key=lambda row: int(_num(row.get("score"))), reverse=True)
    out = []
    for row in scored[:n]:
        gn = as_int(row.get("game_number"))
        score = int(_num(row.get("score")))
        out.append(
            _effort_row(
                row,
                label=f"G{(gn or 0) + 1}",
                value=score,
            )
        )
    return out


def _top_pairs(games: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int], dict[int, dict[str, Any]]] = {}
    for row in games:
        key = _player_key(row)
        rn = as_int(row.get("round_number"))
        gn = as_int(row.get("game_number"))
        if not key or rn is None or gn is None:
            continue
        grouped.setdefault((key, rn), {})[gn] = row
    pairs = []
    for scores in grouped.values():
        one_based = sorted(g + 1 for g in scores)
        for start in one_based:
            if start % 2 == 1 and (start + 1) in one_based:
                left = scores[start - 1]
                right = scores[start]
                total = int(_num(left.get("score"))) + int(_num(right.get("score")))
                pairs.append(
                    _effort_row(
                        left,
                        label=f"G{start}+G{start + 1}",
                        value=total,
                    )
                )
    pairs.sort(key=lambda row: int(row["value"]), reverse=True)
    return pairs[:n]


def _top_blocks(games: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    totals: dict[tuple[str, int], dict[str, Any]] = {}
    for row in games:
        key = _player_key(row)
        rn = as_int(row.get("round_number"))
        if not key or rn is None:
            continue
        bucket = totals.setdefault(
            (key, rn),
            {
                "player_name": row.get("player_name"),
                "club": row.get("club"),
                "round_name": row.get("round_name"),
                "value": 0,
                "games": 0,
            },
        )
        score = int(_num(row.get("score")))
        bucket["value"] += score
        if score > 0:
            bucket["games"] += 1
    blocks = []
    for bucket in totals.values():
        avg = bucket["value"] / max(int(bucket["games"]), 1)
        blocks.append(
            {
                "player": as_str(bucket.get("player_name")) or "",
                "club": as_str(bucket.get("club")) or "",
                "stage": as_str(bucket.get("round_name")) or "",
                "label": "Block",
                "value": int(bucket["value"]),
                "display_value": f"{int(bucket['value'])} (\u2300{avg:.1f})",
            }
        )
    blocks.sort(key=lambda row: int(row["value"]), reverse=True)
    return blocks[:n]


def _summary_cards(
    event: str,
    games: list[dict[str, Any]],
    leaderboard: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    *,
    round_number: int | None,
    use_net: bool,
) -> list[dict[str, Any]]:
    latest = rounds[-1] if rounds else None
    card_round = round_number if round_number is not None else (latest["round_number"] if latest else None)
    cards: list[dict[str, Any]] = [
        {"title": "Tournament", "value": event, "type": "stat"},
    ]
    if latest:
        cards.append(
            {
                "title": "Current Round",
                "value": latest.get("round_name") or f"Round {latest.get('round_number')}",
                "subtitle": f"#{latest.get('round_number')}" if latest.get("round_number") else "",
                "type": "stat",
            }
        )
    leader = leaderboard[0] if leaderboard else None
    pins_label = "pins (netto)" if use_net else "pins"
    if leader:
        total = leader.get("total_net") if use_net else leader.get("total_score")
        avg = leader.get("avg_net") if use_net else leader.get("avg_scratch")
        subtitle = f"\u2300{avg:.1f} ({total} {pins_label})" if avg is not None else f"{total} {pins_label}"
        cards.append(
            {
                "title": "Tournament Leader",
                "value": leader.get("player") or leader.get("player_name"),
                "subtitle": subtitle,
                "type": "stat",
            }
        )
    cards.append({"title": "Participants", "value": len(leaderboard), "subtitle": "Field size", "type": "stat"})
    if card_round is not None:
        stage_games = [row for row in games if as_int(row.get("round_number")) == card_round]
        stage = _aggregate_player(stage_games)
        best = None
        best_total = -1
        for bucket in stage.values():
            rnd = (bucket.get("rounds") or {}).get(card_round) or {"scratch": 0, "net": 0.0}
            total = rnd["net"] if use_net else rnd["scratch"]
            if total > best_total:
                best_total = total
                best = bucket
        if best:
            rnd = (best.get("rounds") or {}).get(card_round) or {"scratch": 0, "net": 0.0, "name": ""}
            name = next((r["round_name"] for r in rounds if r["round_number"] == card_round), f"Round {card_round}")
            total = _arith_round_int(rnd["net"]) if use_net else int(rnd["scratch"])
            cards.append(
                {
                    "title": "Stage Winner",
                    "value": best["player"],
                    "subtitle": f"{name}: {total} {pins_label}",
                    "type": "stat",
                }
            )
    return cards


def _format_info(
    games: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    *,
    use_net: bool,
    season: str = "",
    event: str = "",
) -> dict[str, Any]:
    hcp = [ _num(row.get("handicap")) for row in games if row.get("handicap") is not None ]
    apriori = [ _num(row.get("apriori_average")) for row in games if row.get("apriori_average") is not None ]
    href = [ _num(row.get("handicap_reference")) for row in games if row.get("handicap_reference") is not None ]
    payload = {
        "round_count": len(rounds),
        "rounds": [{**item} for item in rounds],
        "handicap": {
            "used": use_net,
            "columns": {
                "handicap": bool(hcp),
                "apriori_average": bool(apriori),
                "handicap_reference": bool(href),
            },
            "pins": _band_int(hcp) if hcp else None,
            "a_priori_average": _band_float(apriori) if apriori else None,
            "handicap_reference": _band_float(href) if href else None,
        },
        "ko_finale_round_number_in_data": None,
        "qualifying_cut_span": None,
        "qualifying_cut_pair": None,
        "qualifying_stages": [],
        "config": {},
    }
    if season and event:
        payload.update(format_ko_fields(season, event, games))
    return payload


def _players_from_games(games: list[dict[str, Any]], *, round_number: int | None) -> list[dict[str, Any]]:
    work = games if round_number is None else [row for row in games if as_int(row.get("round_number")) == round_number]
    by_key: dict[str, dict[str, Any]] = {}
    for row in work:
        key = _player_key(row)
        name = as_str(row.get("player_name")) or ""
        if not key or not name:
            continue
        if key not in by_key:
            pid = as_str(row.get("player_id")) or ""
            by_key[key] = {"id": pid if pid.casefold() not in {"0", "nan", "none"} else "", "name": name}
    return sorted(by_key.values(), key=lambda row: row["name"].casefold())


def _remap_field_progress(
    field: dict[str, Any],
    orig_names: list[tuple[str | None, str | None]],
    games: list[dict[str, Any]],
) -> dict[str, Any]:
    series = dict(field.get("player_rank_series") or {})
    if not series:
        return field
    orig_to_key: dict[str, str] = {}
    for pid, name in orig_names:
        if name:
            orig_to_key[name] = pid or name
    canon_by_key: dict[str, str] = {}
    for row in games:
        key = _player_key(row)
        name = as_str(row.get("player_name")) or ""
        if key and name:
            canon_by_key[key] = name
    remapped: dict[str, list[int]] = {}
    for orig, values in series.items():
        key = orig_to_key.get(orig, orig)
        dest = canon_by_key.get(key, orig)
        if dest in remapped:
            continue
        remapped[dest] = list(values)
    field = dict(field)
    field["player_rank_series"] = remapped
    return field


def _player_round_table_from_games(
    games: list[dict[str, Any]],
    player_key: str,
    *,
    use_net: bool,
) -> list[dict[str, Any]]:
    if not player_key:
        return []
    work = [row for row in games if not _is_walkover(row)]
    rounds = _rounds_from_games(work)
    game_numbers = sorted({gn for row in work if (gn := as_int(row.get("game_number"))) is not None})
    by_player_round: dict[tuple[str, int], dict[str, Any]] = {}
    for row in work:
        key = _player_key(row)
        rn = as_int(row.get("round_number"))
        gn = as_int(row.get("game_number"))
        if not key or rn is None or gn is None:
            continue
        slot = by_player_round.setdefault(
            (key, rn),
            {
                "player": as_str(row.get("player_name")) or "",
                "player_id": as_str(row.get("player_id")),
                "stage": as_str(row.get("round_name")) or f"Round {rn}",
                "round_number": rn,
                "games": {},
                "handicap": [],
            },
        )
        score = int(_num(row.get("score")))
        hcp = _num(row.get("handicap"))
        slot["games"][gn] = score
        slot["handicap"].append(hcp)
        slot[f"game_{gn}"] = score
    rows = []
    running_sc = 0
    running_net = 0.0
    running_games = 0
    for item in rounds:
        rn = item["round_number"]
        slot = by_player_round.get((player_key, rn))
        if not slot:
            continue
        stage_score = sum(slot["games"].values())
        stage_net = stage_score + sum(slot["handicap"])
        games_n = max(sum(1 for v in slot["games"].values() if v > 0), 1)
        running_sc += stage_score
        running_net += stage_net
        running_games += games_n
        stage_metric: dict[str, float] = {}
        cum_metric: dict[str, float] = {}
        for key, other in by_player_round.items():
            pins = sum(other["games"].values()) + (sum(other["handicap"]) if use_net else 0)
            if key[1] == rn:
                stage_metric[key[0]] = pins
            if key[1] <= rn:
                cum_metric[key[0]] = cum_metric.get(key[0], 0) + pins
        stage_keys = list(stage_metric)
        cum_keys = list(cum_metric)
        round_rank = (
            _min_rank([float(stage_metric[k]) for k in stage_keys])[stage_keys.index(player_key)]
            if player_key in stage_metric
            else None
        )
        cum_rank = (
            _min_rank([float(cum_metric[k]) for k in cum_keys])[cum_keys.index(player_key)]
            if player_key in cum_metric
            else None
        )
        out = {
            **{k: v for k, v in slot.items() if k not in {"games", "handicap"}},
            "round_hcp": _handicap_label(slot["handicap"]) if use_net else None,
            "stage_score": int(stage_score),
            "score_total": int(stage_score),
            "stage_avg": round(stage_score / games_n, 1),
            "round_avg": round(stage_score / games_n, 1),
            "round_rank": round_rank,
            "cum_score": running_sc,
            "cum_avg": round(running_sc / max(running_games, 1), 1),
            "cum_avg_sc": round(running_sc / max(running_games, 1), 1),
            "cum_rank": cum_rank,
        }
        if use_net:
            out["stage_net"] = _arith_round_int(stage_net)
            out["stage_avg_net"] = round(stage_net / games_n, 1)
            out["overall_net"] = _arith_round_int(running_net)
            out["overall_avg_net"] = round(running_net / max(running_games, 1), 1)
        for gn in game_numbers:
            out.setdefault(f"game_{gn}", slot["games"].get(gn, ""))
        rows.append(out)
    return rows


def _player_progress(field: dict[str, Any], games: list[dict[str, Any]]) -> dict[str, Any]:
    slots = field.get("game_slots") or []
    length_map = {int(k): int(v) for k, v in (field.get("round_length_map") or {}).items()}
    by_round: dict[int, dict[int, int]] = {}
    for row in games:
        rn = as_int(row.get("round_number"))
        gn = as_int(row.get("game_number"))
        if rn is None or gn is None:
            continue
        by_round.setdefault(rn, {})[gn] = int(_num(row.get("score")))
    avg_series: list[float | None] = []
    game_score_series: list[int | None] = []
    round_end_lines: list[int] = []
    cum = 0.0
    n = 0
    for rn, g in slots:
        score = by_round.get(int(rn), {}).get(int(g))
        game_score_series.append(score if score else None)
        if score:
            cum += score
            n += 1
            avg_series.append(round(cum / n, 2))
        else:
            avg_series.append(None)
        if int(g) == length_map.get(int(rn), 0) - 1:
            round_end_lines.append(len(avg_series))
    return {
        "avg_series": avg_series,
        "game_score_series": game_score_series,
        "round_end_lines": round_end_lines,
        "tournament_leader_avg_series": field.get("tournament_leader_avg_series") or [],
        "tournament_lowest_avg_series": field.get("tournament_lowest_avg_series") or [],
    }


def _player_best_efforts(games: list[dict[str, Any]], *, use_net: bool) -> dict[str, Any]:
    empty = {"highest_game": None, "highest_pair": None, "highest_block": None, "handicap_profile": None}
    if not games:
        return empty
    top_game = max(games, key=lambda row: int(_num(row.get("score"))))
    gn = as_int(top_game.get("game_number"))
    highest_game = {
        "score": int(_num(top_game.get("score"))),
        "stage": as_str(top_game.get("round_name")) or "",
        "game": (gn + 1) if gn is not None else None,
    }
    pairs = _top_pairs(games, 1)
    highest_pair = None
    if pairs:
        highest_pair = {"score": pairs[0]["value"], "stage": pairs[0]["stage"], "pair": pairs[0]["label"]}
    blocks = _top_blocks(games, 1)
    highest_block = None
    if blocks:
        highest_block = {"score": blocks[0]["value"], "stage": blocks[0]["stage"]}
    profile = None
    if use_net:
        hcp = [_num(row.get("handicap")) for row in games if row.get("handicap") is not None]
        apriori = [_num(row.get("apriori_average")) for row in games if row.get("apriori_average") is not None]
        href = [_num(row.get("handicap_reference")) for row in games if row.get("handicap_reference") is not None]
        if hcp or apriori or href:
            profile = {
                "handicap_per_game": _arith_round_int(sum(hcp) / len(hcp)) if hcp else None,
                "a_priori_average": round(apriori[0], 1) if apriori else None,
                "handicap_reference": round(href[0], 1) if href else None,
            }
    return {
        "highest_game": highest_game,
        "highest_pair": highest_pair,
        "highest_block": highest_block,
        "handicap_profile": profile,
    }
