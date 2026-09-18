# Historic games without `player_id`

Found 2026-09-18 on Volkmar Hartfeil (EDV `7830`) at Donaubowler Regensburg.
**Do not treat this as a query-only bug.** The warehouse rows are incomplete;
the API currently papers over it. Fix it in **data generation / publish**, then
narrow the runtime join.

## What we saw

| Surface | Hartfeil @ Donaubowler |
|---------|------------------------|
| Club player results / legends | **1001** games |
| `/spieler` (id `7830` only) | **~6xx** games, seasons from **10/11** |

Club grouping used `coalesce(player_id, player_name)`, so name-only lines
counted. Player document used `player_id = 7830`, so every row with an empty
id dropped. Those empty-id lines are the missing **08/09** and **09/10**
(and the same pattern likely exists for other long-career players).

Name variants on the same id (e.g. `Hartfeil-Ave, Volkmar`) are a second,
related gap: aliases belong on one EDV id, not as a second person.

## Warehouse fact

`game_line` / `tournament_line` often have a display `player_name` and **no**
`player_id` in older seasons. EDV numbers start appearing later (here 10/11).
That is a publish/normalization hole, not a DuckDB import typing issue.

Homonyms must stay unfilled: only stamp an id when the label uniquely maps to
one catalog player.

## Runtime workaround (temporary)

Until publish backfills ids:

- Player match (`queries/player.py` `_who_for`): `player_id = X` **or**
  (empty `player_id` and name in canonical + aliases).
- Club / all-player rollups: `fold_orphan_player_ids` attaches a catalog id
  when the name uniquely maps, then merges the split groups.

After a real backfill these branches should shrink to id-only.

## Data generation follow-up (not done)

Owner: `bowlyzer_deploy` pipeline / players registry
([`DATA_PIPELINE_PLAN.md` §2.7](../../bowlyzer_deploy/docs/planning/DATA_PIPELINE_PLAN.md)).

1. Audit how many `game_line` / `tournament_line` rows have a name and empty
   `player_id`, by season.
2. At merge/publish, stamp `player_id` from the registry when the name (or
   alias) uniquely maps to one EDV id.
3. Leave unresolved and colliding names in the audit CSV — no silent merge of
   two people.
4. Re-import Parquet → DuckDB and drop the orphan-name join once coverage is
   enough to make id-only player documents match club totals.
