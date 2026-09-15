# DuckDB vs Flask — tournament kernel + club history

Measured 2026-09-15 against published warehouse `20260915T084441Z`.
Compare script: `scripts/compare_queries.py` (Flask services in-process, DuckDB file in this repo).

## Tournament: Bayerische Meisterschaft - Männer Einzel, 25/26

Kernel = overall leaderboard (pins + rank) + field-progress rank series. No KO bracket.

| Engine | Wall clock | Notes |
|--------|------------|--------|
| DuckDB | ~78–98 ms | Typed `tournament_line`, one SQL pass + rank fill |
| Flask pandas | ~73 ms warm / ~125 ms after field-progress cache miss | `get_leaderboard_table` + `get_field_progress` |

Correctness:

- Leaderboard pins: **0 mismatches** (96 named players).
- Flask also emits one empty player name row (ignored).
- Two rank numbers still differ because Flask ranks that empty row; **final field-progress rank matches for every named player**.
- Series length 16 games both sides.

## Club history: BC EMAX Unterföhring

Team × season × league + table position (same grain as `get_club_team_season_matrix`).

| Engine | Wall clock |
|--------|------------|
| DuckDB | **~58–63 ms** |
| Flask pandas | **~27.4 s** |

Correctness: 13 seasons, 91 team-season cells, 47 position cells — **league sets and positions match**.

Club history is the clearer feasibility win (no JSON cache, live after import). Tournament kernel is already in the same tens of milliseconds as cached Flask compute, without a 1-hour warm.
