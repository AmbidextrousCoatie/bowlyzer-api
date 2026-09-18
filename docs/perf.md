# Query performance and live publish

Not a stack problem. Kernels that are one SQL pass are already tens of
milliseconds (`docs/bench-get-section.md`). The slow paths below re-run a
full document **once per season or event** (N+1), or open a new DuckDB
connection per helper. Fix those before considering a language rewrite.

Observed scale: a few dozen users, usually &lt;5 req/s, peaks ~10–20/s.
Warehouse today ~700k `game_line` rows; another ~700k historic games still
to import; ~50k games/year after that.

## Worst N+1 (fix first)

| Call | Where | What happens | Why it hurts | Direction |
|------|--------|----------------|--------------|-----------|
| `GET /api/v1/teams/{team}` | `queries/team.py` `team_document` | **Fixed.** History position + league comparison use `league_table_snapshots`: two grouped queries for all `(season, event)` pairs, same ranking as `league_standings` through latest week, no honor/series/players. | Was ~15 seasons → ~30 full league documents (~3.6s for EPA München 3). | Done. |
| `GET /api/v1/seasons/{season}/standings` | `queries/league.py` `season_standings` | **Fixed.** One session: `league_table_snapshots` + latest week per event + batched honor (top-3 per league for that week). Series/players omitted (SPA season dashboard only uses standings + honor + week). | Was 24 full `league_standings` (~2.0s for 25/26). | Done. |
| `GET /api/v1/tournaments/podiums` | `queries/tournament.py` `tournament_podiums` | **Fixed.** `overall_standings_for` loads all needed `tournament_line` rows in one session, ranks in Python, and runs KO only when config or KO round names exist. | Was 56 events → 56 connections + full KO rebuilds (~2.4s unfiltered). | Done. |
| `GET /api/v1/players/{id}/tournaments` | `queries/player.py` `player_tournaments` | **Fixed.** Same `overall_standings_for` batch for the player’s (season, event) pairs. | Was one `overall_standings` per event (~530 ms for 12 events). | Done. |

`overall_standings` is the right **place** (KO-aware). It is the wrong **grain** to call in a loop.

## Duplicate work on a single tournament request

`GET /api/v1/tournaments/{season}/{event}` (`tournament_document`):

1. `tournament_section` — SQL leaderboard + field-progress (own connection).
2. Reload every `tournament_line` row for the event (second connection).
3. Rebuild the leaderboard in Python and overlay KO; the SQL leaderboard is discarded.

`GET /api/v1/tournaments/.../players/{player}` then runs the **full document** again and loads games a third time.

Direction: one game fetch per request; SQL kernel only for field-progress (or fold that into the same pass); player section as `select` on the document, not a second build.

## Cross-cutting

| Issue | Where | Direction |
|-------|--------|-----------|
| New DuckDB connection per `session()` / `connect()`, and again in `data_revision()` on every HTTP response | `warehouse.py`, `server.py` | Process-level read connection (or a small pool). Stamp revision once per request. |
| `league_standings` always builds honor + series + players | `queries/league.py` | Callers that only need `{team, rank, pins, average}` should not pay for charts. |
| stdlib `ThreadingHTTPServer` | `server.py` | Fine at current RPS. Swap to uvicorn later without touching kernels. |

In-memory loops after **one** fetch (league week series, KO pairing, canonical names) are not the problem.

## What is already fine

- Club history (~60 ms vs ~27 s Flask pandas) — `docs/bench-get-section.md`
- Tournament SQL kernel (leaderboard + field-progress, no KO) ~80–100 ms
- Team document (history + league comparison snapshots, no honor/series) — EPA München 3, 15 seasons: 3.6s → 143 ms (~25×)
- Season standings (all league tables + honor, no series/players) — 25/26, 24 leagues: 2.0s → 380 ms (~5×)
- Tournament podiums (all events, KO-aware) — 56 groups: 2.4s → 757 ms (~3×)
- Player tournament history (Feller, 12 events): 531 ms → 257 ms (~2×)
- Home counts, matchday, compare, records: one or few queries per request
- KO for a **single** event: Python over a few thousand rows is cheap; cost is repeating it per event

## Live ingest and publish (not built)

Acquisition stays out of this API. Stats should read only **published** rows.

Intended league-day flow (notification vs poll still open):

1. Both teams submit their own **and** the opponent’s result.
2. Persist only when the two submissions **verify** (cross-check).
3. On notify (or poll): take the verified update, append, **publish immediately** so the API serves it.
4. Teams have **24h** to report errors to the secretary → review → possible correction and re-publish.
5. **Two days** after the league day, treat that day’s results as stable and run a **full verification** over that day’s set; correct and re-publish if needed.

Until then the warehouse is still “rebuild DuckDB from published Parquet”. Incremental insert + atomic swap (staging file → published file) is enough at &lt;200 verified matches/day. Do not put unverified submissions on the public API.

Historic backfill (~700k games) is a one-shot import, not this path.
