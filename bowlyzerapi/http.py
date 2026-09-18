"""Dispatch /api/v1 paths from docs/resources.md."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import unquote

from bowlyzerapi.queries.club import (
    club_document,
    club_history,
    club_honor_300,
    club_legends,
    club_list,
    club_players,
    club_rankings,
)
from bowlyzerapi.queries.home import home
from bowlyzerapi.queries.league import (
    compare,
    league_standings,
    matchday,
    records,
    season_standings,
    team_in_league,
    timetable,
)
from bowlyzerapi.queries.meta import catalog
from bowlyzerapi.queries.player import (
    player_aggregate,
    player_document,
    player_highest_games,
    player_seasons,
    player_tournaments,
    search_players,
)
from bowlyzerapi.queries.team import team_document, team_list
from bowlyzerapi.queries.tournament import (
    tournament_document,
    tournament_list,
    tournament_player_section,
    tournament_players,
    tournament_podiums,
)
from bowlyzerapi.warehouse import connect, warehouse_path


Handler = Callable[[dict[str, str], dict[str, str]], tuple[int, dict[str, Any]]]


def health_payload() -> tuple[int, dict[str, Any]]:
    path = warehouse_path()
    if not path.is_file():
        return 503, {
            "status": "empty",
            "error": {
                "code": "warehouse_missing",
                "message": f"No warehouse at {path}. Run: uv run python scripts/run_import.py",
            },
        }

    con = connect(read_only=True)
    try:
        tables: dict[str, int] = {}
        for name in ("game_line", "tournament_line", "player", "club", "affiliation", "verein"):
            exists = con.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?",
                [name],
            ).fetchone()
            if exists:
                tables[name] = int(con.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            else:
                tables[name] = 0
        meta = {}
        meta_exists = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'warehouse_meta'"
        ).fetchone()
        if meta_exists:
            for key, value in con.execute("SELECT key, value FROM warehouse_meta").fetchall():
                meta[str(key)] = value
    finally:
        con.close()

    return 200, {
        "status": "ok",
        "warehouse": str(path.resolve()),
        "revision": meta.get("source_run_id") or meta.get("imported_at"),
        "meta": meta,
        "tables": tables,
    }


def parse_segments(raw_path: str) -> list[str]:
    path = raw_path.split("?", 1)[0]
    return [unquote(part) for part in path.strip("/").split("/") if part]


def match_route(segments: list[str], pattern: list[str]) -> dict[str, str] | None:
    if len(segments) != len(pattern):
        return None
    params: dict[str, str] = {}
    for got, want in zip(segments, pattern, strict=True):
        if want.startswith("{") and want.endswith("}"):
            params[want[1:-1]] = got
        elif got != want:
            return None
    return params


def _int(qs: dict[str, str], key: str) -> int | None:
    raw = (qs.get(key) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _missing(message: str) -> tuple[int, dict[str, Any]]:
    return 400, {"error": {"code": "missing_parameters", "message": message}}


def h_health(_p, _q):
    return health_payload()


def h_meta(_p, qs):
    return 200, catalog(
        season=qs.get("season") or None,
        league=qs.get("league") or None,
        week=_int(qs, "week"),
        club=qs.get("club") or None,
        team=qs.get("team") or None,
    )


def h_home(_p, qs):
    return 200, home(limit=_int(qs, "limit") or 8)


def h_season_standings(p, qs):
    season = (p.get("season") or qs.get("season") or "").strip()
    if not season:
        return _missing("Season is required.")
    return 200, season_standings(season)


def h_leagues(_p, qs):
    data = catalog(season=qs.get("season") or None)
    return 200, {
        "leagues": data["leagues"],
        "seasons": data["seasons"],
        "leagues_by_season": data["leagues_by_season"],
    }


def _season_league(p: dict[str, str], qs: dict[str, str]) -> tuple[str, str] | None:
    season = (p.get("season") or qs.get("season") or "").strip()
    league = (p.get("league") or qs.get("league") or "").strip()
    if not season or not league:
        return None
    return season, league


def h_league_standings(p, qs):
    scoped = _season_league(p, qs)
    if scoped is None:
        return _missing("Season and league are required.")
    season, league = scoped
    return 200, league_standings(season, league, week=_int(qs, "week"), view=qs.get("view") or None)


def h_timetable(p, qs):
    scoped = _season_league(p, qs)
    if scoped is None:
        return _missing("Season and league are required.")
    season, league = scoped
    return 200, timetable(season, league)


def h_matchday(p, qs):
    scoped = _season_league(p, qs)
    if scoped is None:
        return _missing("Season and league are required.")
    season, league = scoped
    return 200, matchday(
        season,
        league,
        int(p["week"]),
        team=qs.get("team") or None,
        round_number=_int(qs, "round"),
        view=qs.get("view") or None,
    )


def h_compare(p, qs):
    scoped = _season_league(p, qs)
    if scoped is None:
        return _missing("Season and league are required.")
    season, league = scoped
    return 200, compare(
        season,
        league,
        team_a=qs.get("team_a") or None,
        team_b=qs.get("team_b") or None,
        week=_int(qs, "week"),
    )


def h_league_team(p, qs):
    scoped = _season_league(p, qs)
    if scoped is None:
        return _missing("Season and league are required.")
    team = (p.get("team") or qs.get("team") or "").strip()
    if not team:
        return _missing("Team is required.")
    season, league = scoped
    return 200, team_in_league(season, league, team)


def h_records(p, qs):
    league = (p.get("league") or qs.get("league") or "").strip()
    if not league:
        return _missing("League is required.")
    season = (p.get("season") or qs.get("season") or "").strip() or None
    return 200, records(season, league, metric=qs.get("metric") or None)


def _flag(qs: dict[str, str], key: str) -> bool:
    return (qs.get(key) or "").strip().lower() in {"1", "true", "yes", "on"}


def h_clubs(_p, qs):
    return 200, club_list(unnumbered=_flag(qs, "unnumbered"))


def h_club_rankings(_p, qs):
    return 200, club_rankings(top_n=_int(qs, "top_n") or 5)


def h_club_history_query(_p, qs):
    club = (qs.get("club") or "").strip()
    if not club:
        return _missing("Query param club is required.")
    return 200, club_history(club)


def h_club_legends_query(_p, qs):
    club = (qs.get("club") or "").strip()
    if not club:
        return _missing("Query param club is required.")
    return 200, club_legends(club, season=qs.get("season") or None)


def h_club_players_query(_p, qs):
    club = (qs.get("club") or "").strip()
    if not club:
        return _missing("Query param club is required.")
    return 200, club_players(club, season=qs.get("season") or None)


def h_club(p, qs):
    return 200, club_document(p["club"], season=qs.get("season") or None)


def h_club_history(p, _q):
    return 200, club_history(p["club"])


def h_club_legends(p, qs):
    return 200, club_legends(p["club"], season=qs.get("season") or None)


def h_club_players(p, qs):
    return 200, club_players(p["club"], season=qs.get("season") or None)


def h_club_honor(p, _q):
    return 200, club_honor_300(p.get("club"))


def h_honor(_p, qs):
    return 200, club_honor_300(qs.get("club") or None)


def _team_threshold(qs: dict[str, str]) -> int:
    return _int(qs, "threshold") or _int(qs, "clutch_threshold") or 10


def h_teams(_p, _q):
    return 200, team_list()


def h_team_query(_p, qs):
    team = (qs.get("team") or qs.get("team_name") or "").strip()
    if not team:
        return _missing("Query param team is required.")
    return 200, team_document(
        team,
        season=qs.get("season") or None,
        threshold=_team_threshold(qs),
    )


def h_team(p, qs):
    return 200, team_document(
        p["team"],
        season=qs.get("season") or None,
        threshold=_team_threshold(qs),
    )


def _player_ident(qs: dict[str, str]) -> str:
    return (qs.get("player_id") or qs.get("player") or qs.get("player_name") or "").strip()


def h_players(_p, qs):
    return 200, search_players(
        q=qs.get("q") or qs.get("search") or None,
        club=qs.get("club") or None,
        limit=_int(qs, "limit"),
    )


def h_player_stats(_p, qs):
    ident = _player_ident(qs)
    club = qs.get("club") or None
    season = qs.get("season") or None
    if ident:
        return 200, player_document(ident, club=club, season=season, top_n=_int(qs, "top_n"))
    return 200, player_aggregate(club=club, season=season, top_n=_int(qs, "top_n"))


def h_player_seasons(_p, qs):
    return 200, player_seasons(ident=_player_ident(qs) or None, club=qs.get("club") or None)


def h_player_highlights(_p, qs):
    return 200, player_highest_games(
        ident=_player_ident(qs) or None,
        club=qs.get("club") or None,
        season=qs.get("season") or None,
        limit=_int(qs, "limit") or 10,
    )


def h_player(p, qs):
    return 200, player_document(p["id"], club=qs.get("club") or None, season=qs.get("season") or None)


def h_player_tournaments(p, qs):
    return 200, player_tournaments(
        p["id"],
        season=qs.get("season") or None,
        event=qs.get("event") or qs.get("tournament") or None,
    )


def h_player_tournaments_query(_p, qs):
    ident = (qs.get("player") or qs.get("id") or "").strip()
    if not ident:
        return _missing("Query param player is required.")
    return 200, player_tournaments(
        ident,
        season=qs.get("season") or None,
        event=qs.get("event") or qs.get("tournament") or None,
    )


def h_tournaments(_p, qs):
    return 200, tournament_list(
        season=qs.get("season") or None,
        club=qs.get("club") or None,
        event=qs.get("event") or qs.get("tournament") or None,
    )


def h_podiums(_p, qs):
    return 200, tournament_podiums(
        season=qs.get("season") or None,
        club=qs.get("club") or None,
        event=qs.get("event") or qs.get("tournament") or None,
        n=_int(qs, "n") or 3,
    )


def h_tournament_players(_p, qs):
    return 200, tournament_players(
        season=qs.get("season") or None,
        event=qs.get("event") or qs.get("tournament") or None,
        round=_int(qs, "round"),
    )


def h_tournament_section(_p, qs):
    season = (qs.get("season") or "").strip()
    event = (qs.get("event") or qs.get("tournament") or "").strip()
    if not season or not event:
        return _missing("Query params season and event are required.")
    return 200, tournament_document(season, event, round=_int(qs, "round"), n=_int(qs, "n") or 5)


def h_tournament(p, qs):
    return 200, tournament_document(
        p["season"],
        p["event"],
        round=_int(qs, "round"),
        n=_int(qs, "n") or 5,
    )


def h_tournament_player(p, _q):
    return 200, tournament_player_section(p["season"], p["event"], p["player"])


def h_tournament_player_query(_p, qs):
    season = (qs.get("season") or "").strip()
    event = (qs.get("event") or qs.get("tournament") or "").strip()
    player = (qs.get("player") or "").strip()
    if not season or not event or not player:
        return _missing("Query params season, event, and player are required.")
    return 200, tournament_player_section(season, event, player)


# Static segments before parameterized siblings.
ROUTES: list[tuple[list[str], Handler]] = [
    (["api", "v1", "health"], h_health),
    (["api", "v1", "meta"], h_meta),
    (["api", "v1", "home"], h_home),
    (["api", "v1", "honor", "300"], h_honor),
    (["api", "v1", "seasons", "standings"], h_season_standings),
    (["api", "v1", "seasons", "{season}", "standings"], h_season_standings),
    (["api", "v1", "leagues", "standings"], h_league_standings),
    (["api", "v1", "leagues", "timetable"], h_timetable),
    (["api", "v1", "leagues", "compare"], h_compare),
    (["api", "v1", "leagues", "records"], h_records),
    (["api", "v1", "leagues", "team"], h_league_team),
    (["api", "v1", "leagues", "matchdays", "{week}"], h_matchday),
    (["api", "v1", "leagues", "{league}", "records"], h_records),
    (["api", "v1", "leagues", "{season}", "{league}", "standings"], h_league_standings),
    (["api", "v1", "leagues", "{season}", "{league}", "timetable"], h_timetable),
    (["api", "v1", "leagues", "{season}", "{league}", "matchdays", "{week}"], h_matchday),
    (["api", "v1", "leagues", "{season}", "{league}", "compare"], h_compare),
    (["api", "v1", "leagues", "{season}", "{league}", "records"], h_records),
    (["api", "v1", "leagues", "{season}", "{league}", "team"], h_league_team),
    (["api", "v1", "leagues"], h_leagues),
    (["api", "v1", "clubs", "rankings"], h_club_rankings),
    (["api", "v1", "clubs", "history"], h_club_history_query),
    (["api", "v1", "clubs", "legends"], h_club_legends_query),
    (["api", "v1", "clubs", "players"], h_club_players_query),
    (["api", "v1", "clubs", "{club}", "history"], h_club_history),
    (["api", "v1", "clubs", "{club}", "legends"], h_club_legends),
    (["api", "v1", "clubs", "{club}", "players"], h_club_players),
    (["api", "v1", "clubs", "{club}", "honor", "300"], h_club_honor),
    (["api", "v1", "clubs", "{club}"], h_club),
    (["api", "v1", "clubs"], h_clubs),
    (["api", "v1", "teams", "document"], h_team_query),
    (["api", "v1", "teams", "{team}"], h_team),
    (["api", "v1", "teams"], h_teams),
    (["api", "v1", "players", "stats"], h_player_stats),
    (["api", "v1", "players", "document"], h_player_stats),
    (["api", "v1", "players", "seasons"], h_player_seasons),
    (["api", "v1", "players", "highlights"], h_player_highlights),
    (["api", "v1", "players", "games"], h_player_highlights),
    (["api", "v1", "players", "tournaments"], h_player_tournaments_query),
    (["api", "v1", "players", "{id}", "tournaments"], h_player_tournaments),
    (["api", "v1", "players", "{id}"], h_player),
    (["api", "v1", "players"], h_players),
    (["api", "v1", "tournaments", "podiums"], h_podiums),
    (["api", "v1", "tournaments", "players"], h_tournament_players),
    (["api", "v1", "tournaments", "player"], h_tournament_player_query),
    (["api", "v1", "tournaments", "section"], h_tournament_section),
    (["api", "v1", "tournaments", "{season}", "{event}", "players", "{player}"], h_tournament_player),
    (["api", "v1", "tournaments", "{season}", "{event}"], h_tournament),
    (["api", "v1", "tournaments"], h_tournaments),
]

ALIASES = {
    ("health",): ["api", "v1", "health"],
    ("healthz",): ["api", "v1", "health"],
}


def dispatch(raw_path: str, qs: dict[str, str]) -> tuple[int, dict[str, Any]]:
    segments = parse_segments(raw_path)
    if tuple(segments) in ALIASES:
        segments = ALIASES[tuple(segments)]
    for pattern, handler in ROUTES:
        params = match_route(segments, pattern)
        if params is None:
            continue
        return handler(params, qs)
    return 404, {
        "error": {
            "code": "not_found",
            "message": "Unknown /api/v1 path. See openapi/openapi.yaml and docs/resources.md.",
        }
    }
