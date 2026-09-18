# Flask RPC → `/api/v1`

Cutover is **new `/api/v1` only**. The React app in `bowlyzer_deploy` keeps
calling Flask until each hook is rewritten. This file is the necessity pass:
what to keep (reshaped), what to merge, what to drop / leave on Flask.

Identifiers in v1 are names the SPA already uses (`season=25/26`, club labels,
tournament titles). Integer FKs stay inside DuckDB. One warehouse — no
`?database=` and no player-hybrid `concat`.

Chart series configs and table **column logic** stay as today; v1 does not
redesign palettes or Tabulator fields.

Contract: [`openapi/openapi.yaml`](../openapi/openapi.yaml). Spike results:
[`bench-get-section.md`](bench-get-section.md). Slow kernels and live-publish
notes: [`perf.md`](perf.md).

## Status

| v1 resource | Kernel | Notes |
|-------------|--------|--------|
| `GET /api/v1/health` | yes | Row counts + revision |
| `GET /api/v1/meta` | yes | seasons, leagues, weeks, teams, rounds, tournaments |
| `GET /api/v1/home` | yes | SPA home on v1 (`counts` + `latest_events`) |
| `GET /api/v1/seasons/{season}/standings` | yes | All leagues in a season (season dashboard) |
| `GET /api/v1/leagues` | yes | Catalog; optional `?season=` |
| `GET /api/v1/leagues/{season}/{league}/standings` | yes | Standings + honor + series + players; `view=history\|averages` |
| `GET /api/v1/leagues/{season}/{league}/timetable` | yes | Week / date / location |
| `GET /api/v1/leagues/{season}/{league}/matchdays/{week}` | yes | Table, honor, games, players; `?team=&view=classic\|individual\|h2h`; `?round=` game details |
| `GET /api/v1/leagues/{season}/{league}/compare` | yes | Team vs opponent cells; `?week=` |
| `GET /api/v1/leagues/team` | yes | Team-in-league performance + win% |
| `GET /api/v1/leagues/{league}/records` | yes | Cross-season; optional `?season=` and `?metric=` |
| `GET /api/v1/clubs` | yes | Club list |
| `GET /api/v1/clubs/rankings` | yes | Pinfall, members, averages, wins |
| `GET /api/v1/clubs/{club}` | yes | Legends + history |
| `GET /api/v1/clubs/{club}/history` | yes | Club matrix grain |
| `GET /api/v1/clubs/{club}/players` | yes | Club player results |
| `GET /api/v1/clubs/{club}/honor/300` | yes | Perfect games |
| `GET /api/v1/honor/300` | yes | Optional `?club=` |
| `GET /api/v1/teams` | yes | Team list |
| `GET /api/v1/teams/{team}` | yes | SPA `/club` on v1 (`?season=` `?threshold=`; query alias `/teams/document`) |
| `GET /api/v1/players` | yes | `?q=&club=` |
| `GET /api/v1/players/{id}` | yes | Lifetime + competitions + highlights |
| `GET /api/v1/players/{id}/tournaments` | yes | Positions use KO / stepladder places when the event has a bracket |
| `GET /api/v1/tournaments` | yes | Catalog; `?season=&club=&event=` returns `seasons` / `events` (group names) |
| `GET /api/v1/tournaments/podiums` | yes | Top-N from overall standings (KO places when the event has a bracket) |
| `GET /api/v1/tournaments/{season}/{event}` | yes | Leaderboard, field-progress, rounds, cards, round_results, best_efforts, format, players, **KO bracket**. `?round=&n=` |
| `GET /api/v1/tournaments/{season}/{event}/players/{player}` | yes | Player section from kernel + KO path / highlights |
| `GET /api/v1/tournaments/players` | yes | Player catalog; `?season=&event=&round=` |
| `GET /api/v1/tournaments/player` | yes | Query alias for player section (`season`, `event`, `player`) |

JSON uses named objects, not Flask `TableData`. KO brackets live on the
tournament document (`ko_bracket`, placements, finale series). Config JSON
stays in the publish dir (`tournament_ko_config.json`), not DuckDB.

## SPA still on Flask (2026-09-17)

Club / player / league / tournament / home / team **stats** hooks are on `/api/v1`.
What remains on Flask is i18n, session, and diagnosis / pipeline (ops).

### i18n (deferred)

| Flask | Caller |
|-------|--------|
| `GET /league/get_translations` | `useTranslations` |
| `POST /league/set_language` | `LanguageContext` |

### Session / data sources (drop from v1 — one warehouse)

| Flask | Caller |
|-------|--------|
| `GET /get-data-sources-info` | `useDatabase` (sidebar Datenquelle) |

Unused by SPA, still Flask: `POST /switch-database`, `GET /debug-session`,
`/reload-data`, `/get-data-source`, `/data-source-changed`,
`/set-season/<season>`, `/test-database-param`, `/test-filter-endpoints`.

### Diagnosis / pipeline (stay on Flask)

| Flask | Caller |
|-------|--------|
| `GET /league/get_week_matrix` | `useWeekMatrix` |
| `GET /league/get_data_oddities` | `useDataOddities` |
| `GET /pipeline/status` | `usePipelineStatus` |
| `GET /pipeline/club_name_validation` | `useClubNameValidation` |
| `POST /pipeline/club_name_validation/save` | `useClubNameValidation` |
| `GET /pipeline/league_standings_validation` | `useLeagueStandingsValidation` |
| `GET /pipeline/tournament_coverage` | `useTournamentCoverage` |
| `GET /pipeline/tournament_source_pdf` | Tournament validation page |

Query aliases (`/leagues/standings?season=&league=`, `/leagues/timetable`,
`/leagues/compare`, `/leagues/matchdays/{week}`, `/leagues/records?league=`,
`/tournaments/section?season=&event=&round=`, `/tournaments/player?season=&event=&player=`, `/clubs/history?club=`, `/teams/document?team=`) skip `%2F` in
path seasons. Season-first identity is `/leagues/{season}/{league}/…`, parallel
to `/tournaments/{season}/{event}`. Cross-season records live at
`/leagues/{league}/records`.

## Resource map (target)

| v1 | Replaces | Merge notes |
|----|----------|-------------|
| `GET /api/v1/meta` | seasons, leagues, weeks, teams, rounds, data-sources-info | Filter catalog. No session switcher. **Not specified yet.** |
| `GET /api/v1/home` | `/home/stats`, `/league/get_latest_events` | Landing aggregate |
| `GET /api/v1/leagues/{season}/{league}/standings` | season league standings, honor scores, team points/positions/averages, league history table | Query `week`, `view=` |
| `GET /api/v1/leagues/{season}/{league}/matchdays/{week}` | week table, game overview, team details (classic / individual / H2H), honor scores, rounds | One matchday document or tight subresources |
| `GET /api/v1/clubs` / `…/clubs/{club}` | club matrix, legends, rankings, player results | Club is first-class (not `/league/get_club_*`) |
| `GET /api/v1/clubs/{club}/history` | `/league/get_club_matrix` | **Shipped** (kernel) |
| `GET /api/v1/teams/{team}` | team history, clutch, consistency, special matches, league comparison | Merge overlapping chart payloads the SPA already combines |
| `GET /api/v1/players` / `…/players/{id}` | search, lifetime, seasons, highest games, club 300 | Filter `club`, `season` |
| `GET /api/v1/tournaments/{season}/{event}` | **section** (cards + leaderboard + rounds + field progress + KO) | Keep as **one document**. Split only if payloads stay huge |
| `GET /api/v1/tournaments/{season}/{event}/players/{player}` | `get_player_section` | Per-player progress + KO path |
| Shared table envelope | 15× `TableData` RPCs | `{ columns, rows, sort }` — later; kernel uses named JSON rows |
| Shared series envelope | points / positions / averages / history charts | `{ categories, series[] }` — later |

## Decision key

| Decision | Meaning |
|----------|---------|
| **keep** | Capability moves to v1 (new URL, not a `get_*` clone) |
| **merge** | Fold into a parent resource; do not keep a dedicated RPC |
| **drop** | Not in v1. Leave on Flask or delete with Jinja |
| **defer** | Needed eventually; do not block the tournament/club kernels |

SPA caller is the React hook (or diagnosis page) that fetches the Flask path
today.

---

## Tournament — `frontend/src/hooks/useTournament.ts`

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /tournament/get_section` | `useTournamentSection` | **keep** (grain is right) | `GET /api/v1/tournaments/{season}/{event}` |
| `GET /tournament/get_summary_cards` | unused by SPA (folded in section) | **merge** | `cards` on the tournament document |
| `GET /tournament/get_leaderboard` | unused by SPA | **merge** | `leaderboard` |
| `GET /tournament/get_round_results` | unused by SPA | **merge** | `round_results` |
| `GET /tournament/get_player_section` | `useTournamentPlayerSection` | **keep** | `…/tournaments/{season}/{event}/players/{player}` |
| `GET /tournament/get_available_seasons` | `useTournamentSeasons` | **keep** / later **merge** into meta | `GET /api/v1/meta` or `GET /api/v1/tournaments?club=` |
| `GET /tournament/get_available_tournaments` | `useTournamentEvents` | **keep** / later **merge** into meta | same |
| `GET /tournament/get_available_rounds` | `useTournamentRounds` | **merge** | `rounds` on the tournament document / format |
| `GET /tournament/get_tournament_format` | `useTournamentFormat` | **merge** | format + handicap + cut metadata on the document |
| `GET /tournament/get_available_players` | `useTournamentPlayers` | **merge** | player list from leaderboard / `?players=` |
| `GET /tournament/get_tournament_podiums` | `useTournamentPodiums` | **keep** | `GET /api/v1/tournaments/podiums?season=&club=` (later) |
| `GET /tournament/get_player_tournament_results` | `usePlayerTournamentResults` | **keep** | player document or `…/players/{id}/tournaments` |

SPA tournament hooks are on v1, including KO (`ko_bracket` on the tournament
document / player section). Config JSON stays in the publish dir, not DuckDB.

---

## League — `frontend/src/hooks/useLeague.ts`

### Filters and season overview

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /league/get_available_seasons` | `useAvailableSeasons` | **keep** | `GET /api/v1/meta` (`seasons`) |
| `GET /league/get_available_leagues` | `useAvailableLeagues` | **keep** | `GET /api/v1/leagues` or `GET /api/v1/meta` (`leagues`, optional `?season=`) |
| `GET /league/get_available_weeks` | `useAvailableWeeks` | **merge** | standings / matchday catalog |
| `GET /league/get_available_teams` | `useAvailableTeams` | **merge** | same |
| `GET /league/get_available_rounds` | `useAvailableRounds` | **merge** | matchday document |
| `GET /league/get_season_league_standings` | `useSeasonLeagueStandings` | **keep** | `GET /api/v1/seasons/{season}/standings` (all leagues) or `GET /api/v1/leagues/{season}/{league}/standings` |
| `GET /league/get_league_history` | `useLeagueHistory` | **keep** | standings resource, `view=history` |
| `GET /league/get_season_timetable` | `useSeasonTimetable` | **keep** | `GET /api/v1/leagues/{season}/{league}/timetable` |

### Matchday / team-in-week

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /league/get_league_week_table` | `useLeagueWeekTable` | **merge** | matchday document `table` |
| `GET /league/get_honor_scores` | `useHonorScores` | **merge** | matchday `honor_scores` (also nested in season standings today) |
| `GET /league/get_team_week_details_table` | `useTeamWeekDetails` classic | **merge** | matchday `view=classic` |
| `GET /league/get_team_individual_scores_table` | `useTeamWeekDetails` individual | **merge** | `view=individual` |
| `GET /league/get_team_week_head_to_head_table` | `useTeamWeekDetails` H2H | **merge** | `view=h2h` |
| `GET /league/get_game_overview` | `useGameOverview` | **merge** | matchday `games` |
| `GET /league/get_game_team_details` | `useGameTeamDetails` | **merge** | matchday game subresource |

### Team-in-league charts / tables

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /league/get_team_points` | `useTeamPoints` | **merge** | league document `series.points` |
| `GET /league/get_team_positions` | `useTeamPositions` | **merge** | `series.positions` |
| `GET /league/get_team_averages` | `useTeamAverages` | **merge** | `series.averages` |
| `GET /league/get_team_analysis` | `useTeamAnalysis` | **merge** | team-in-league block |
| `GET /league/get_team_performance_table` | `useTeamPerformanceTable` | **merge** | same |
| `GET /league/get_team_win_percentage_table` | `useTeamWinPercentageTable` | **merge** | same |
| `GET /league/get_individual_averages` | `useIndividualAverages` | **keep** | league document or player list `view=averages` |
| `GET /league/get_team_vs_team_comparison` | `useTeamVsTeamComparison` | **keep** | `GET /api/v1/leagues/{season}/{league}/compare?team_a=&team_b=` |

Same aggregation, different `order_by` — **merge** into one records resource
with `metric=`:

| Flask | Caller | Decision |
|-------|--------|----------|
| `GET /league/get_league_averages_history` | `useLeagueAveragesHistory` | **merge** (`metric=averages_history`) |
| `GET /league/get_points_to_win_history` | `usePointsToWinHistory` | **merge** |
| `GET /league/get_top_team_performances` | `useTopTeamPerformances` | **merge** |
| `GET /league/get_top_individual_performances` | `useTopIndividualPerformances` | **merge** |
| `GET /league/get_record_games` | `useRecordGames` | **merge** |
| `GET /league/get_record_individual_games` | `useRecordIndividualGames` | **merge** |
| `GET /league/get_record_team_games` | `useRecordTeamGames` | **merge** |

### Club (served under `/league/*` today)

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /league/get_club_matrix` | `useClubMatrix`, `useMyClub` | **keep** | `GET /api/v1/clubs/{club}/history` (**shipped**) |
| `GET /league/get_club_legends` | `useClubLegends` | **keep** | `GET /api/v1/clubs/{club}` (`legends`) |
| `GET /league/get_club_player_results` | `useClubPlayerResults` | **keep** | `GET /api/v1/clubs/{club}/players` |
| `GET /league/get_club_rankings` | `useClubRankings` | **keep** | `GET /api/v1/clubs/rankings` |

`useMyClub` only needs the matrix for participation filtering — no extra RPC.

### Diagnosis (league-prefixed, not stats v1)

| Flask | Caller | Decision |
|-------|--------|----------|
| `GET /league/get_week_matrix` | `useWeekMatrix` | **drop** from v1 — stay on Flask diagnosis |
| `GET /league/get_data_oddities` | `useDataOddities` | **drop** from v1 — stay on Flask diagnosis |

---

## Team — `frontend/src/hooks/useTeam.ts`

SPA cutover 2026-09-16. List is `GET /api/v1/teams`. Seasons / history / leagues share
the unfiltered team document; clutch / consistency / special matches share
`?season=` (and clutch `?threshold=` when not 10). Query alias `/api/v1/teams/document?team=`.

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /team/get_teams` | `useTeams` | **merge** | `GET /api/v1/meta` (`teams`) or `GET /api/v1/teams` |
| `GET /team/get_available_seasons` | `useTeamSeasons` | **merge** | team document `seasons` |
| `GET /team/get_team_history` | `useTeamHistory` | **keep** | `GET /api/v1/teams/{team}` |
| `GET /team/get_league_comparison` | `useLeagueComparison` | **merge** | team document `leagues` |
| `GET /team/get_clutch_analysis` | `useClutchAnalysis` | **merge** | team document `clutch` (`?threshold=`) |
| `GET /team/get_consistency_metrics` | `useConsistencyMetrics` | **merge** | `consistency` |
| `GET /team/get_special_matches` | `useSpecialMatches` | **merge** | `special_matches` |

Flask-only, no SPA hook: `GET /team/get_available_weeks`, `GET /team/get_margin_analysis` — **drop** or fold if a page still uses them via Jinja.

---

## Player — `frontend/src/hooks/usePlayer.ts`

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /player/search` | `usePlayerSearch` | **keep** | `GET /api/v1/players?q=&club=` |
| `GET /player/get_available_seasons` | `usePlayerSeasons` | **merge** | player document `seasons` |
| `GET /player/get_lifetime_stats` | `usePlayerLifetime` | **keep** | `GET /api/v1/players/{id}` |
| `GET /player/get_highest_individual_games` | `useHighestGames` | **merge** | player document `highlights` |
| `GET /player/get_club_300` | `useClub300` | **keep** | `GET /api/v1/clubs/{club}/honor/300` or `GET /api/v1/honor/300?club=` |

`GET /player/get-stats` — **drop** (legacy). Player hybrid file / runtime concat — **drop**.

---

## Home — `frontend/src/hooks/useHome.ts`

SPA cutover 2026-09-16. Hooks share `GET /api/v1/home?limit=` (raw document + `select`).

| Flask | Caller | Decision | v1 |
|-------|--------|----------|-----|
| `GET /home/stats` | `useHomeStats` | **keep** | `GET /api/v1/home` (`counts`; drop Flask `database` / `tournament_database` fields) |
| `GET /league/get_latest_events` | `useLatestEvents` | **merge** | `GET /api/v1/home` (`latest_events`) |
| `GET /get_latest_events` | none (legacy path) | **drop** | duplicate of the league route |

---

## Session, i18n, pipeline — leave on Flask

### Session / data sources — `useDatabase.ts`

| Flask | Decision |
|-------|----------|
| `GET /get-data-sources-info` | **drop** from v1 (one warehouse) |
| `POST /switch-database` | **drop** |
| `GET /debug-session`, `/reload-data`, `/get-data-source`, `/data-source-changed` | **drop** |
| `GET /test-database-param`, `/test-filter-endpoints` | **drop** |
| `GET /set-season/<season>` | **drop** |

Deep link `?database=` stays a Flask/SPA concern until those hooks move.

### i18n — **defer**

| Flask | Caller | Decision |
|-------|--------|----------|
| `GET /league/get_translations` | `useTranslations` | **defer** — SPA copy later; do not block stats v1 |
| `POST /league/set_language` | `LanguageContext` | **defer** |

### Pipeline / diagnosis — **drop** from v1 (stay on Flask)

| Flask | Caller |
|-------|--------|
| `GET /pipeline/status` | `usePipelineStatus` |
| `GET /pipeline/club_name_validation`, `POST …/save` | `useClubNameValidation` |
| `GET /pipeline/league_standings_validation` | `useLeagueStandingsValidation` |
| `GET /pipeline/tournament_coverage` | `useTournamentCoverage` |
| `GET /pipeline/tournament_source_pdf` | Tournament validation page |

Acquisition, Excel/GF, `rebuild_league_caches.py`, and disk JSON cache stay in
`bowlyzer_deploy`. v1 has no cache warmup: re-run `scripts/run_import.py`
(or Compose `import`) after a publish.

---

## Flask routes with no React hook (drop / Jinja leftover)

| Flask | Notes |
|-------|--------|
| `GET /league/get_available_divisions` | Unused by current SPA |
| `GET /league/get_combinations` | Documented legacy in `league_service_legacy.py` |
| `GET /league/get_team_week_details` | Replaced by `*_table` variants |
| `GET /league/get_team_points_vs_average` | Legacy |
| `GET /tournament/stats`, `/league/stats` | SPA page routes, not JSON |

---

## Envelopes (later, not this phase)

When more resources land, prefer:

- **Table:** `{ columns, rows, sort }` instead of Flask `TableData` groups-of-groups.
  Kernel tournament leaderboard already uses named objects — keep that for new
  JSON; wrap in React tables without copying Tabulator column defs into this repo.
- **Series:** `{ categories, series[] }` for points/positions/averages/history.
- **Errors:** `{ "error": { "code", "message" } }` (already used).
- **Revision:** `X-Data-Revision` on every successful stats response.

## Out of scope

- Porting `league_service.py` / `tournament_service.py` wholesale
- Changing the publish pipeline or Parquet layout
- Verein-level resources
- Redesigning charts or table columns
