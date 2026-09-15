-- Typed warehouse load from published Parquet (all-string on disk today).
-- Placeholders: {{PARQUET_DIR}}  (no trailing slash, POSIX path)

CREATE OR REPLACE TABLE game_line AS
SELECT
    NULLIF(TRIM(CAST("Season" AS VARCHAR)), '') AS season,
    TRY_CAST(NULLIF(TRIM(CAST("Week" AS VARCHAR)), '') AS INTEGER) AS week,
    TRY_CAST(NULLIF(TRIM(CAST("Date" AS VARCHAR)), '') AS DATE) AS game_date,
    TRY_CAST(NULLIF(TRIM(CAST("Players per Team" AS VARCHAR)), '') AS INTEGER) AS players_per_team,
    NULLIF(TRIM(CAST("Location" AS VARCHAR)), '') AS location,
    TRY_CAST(NULLIF(TRIM(CAST("Round Number" AS VARCHAR)), '') AS INTEGER) AS round_number,
    TRY_CAST(NULLIF(TRIM(CAST("Match Number" AS VARCHAR)), '') AS INTEGER) AS match_number,
    NULLIF(TRIM(CAST("Team" AS VARCHAR)), '') AS team,
    TRY_CAST(NULLIF(TRIM(CAST("Position" AS VARCHAR)), '') AS INTEGER) AS position,
    NULLIF(TRIM(CAST("Player" AS VARCHAR)), '') AS player_name,
    NULLIF(TRIM(CAST("Player ID" AS VARCHAR)), '') AS player_id,
    NULLIF(TRIM(CAST("Opponent" AS VARCHAR)), '') AS opponent,
    TRY_CAST(NULLIF(TRIM(CAST("Score" AS VARCHAR)), '') AS INTEGER) AS score,
    TRY_CAST(NULLIF(TRIM(CAST("Points" AS VARCHAR)), '') AS DOUBLE) AS points,
    TRY_CAST(NULLIF(TRIM(CAST("Bonus Points" AS VARCHAR)), '') AS DOUBLE) AS bonus_points,
    CASE lower(TRIM(CAST("Input Data" AS VARCHAR)))
        WHEN 'true' THEN TRUE
        WHEN '1' THEN TRUE
        WHEN 'yes' THEN TRUE
        WHEN 'false' THEN FALSE
        WHEN '0' THEN FALSE
        WHEN 'no' THEN FALSE
        ELSE NULL
    END AS input_data,
    CASE lower(TRIM(CAST("Computed Data" AS VARCHAR)))
        WHEN 'true' THEN TRUE
        WHEN '1' THEN TRUE
        WHEN 'yes' THEN TRUE
        WHEN 'false' THEN FALSE
        WHEN '0' THEN FALSE
        WHEN 'no' THEN FALSE
        ELSE NULL
    END AS computed_data,
    NULLIF(TRIM(CAST("Event" AS VARCHAR)), '') AS event,
    lower(NULLIF(TRIM(CAST("Event Type" AS VARCHAR)), '')) AS event_type,
    NULLIF(TRIM(CAST("Club" AS VARCHAR)), '') AS club
FROM read_parquet('{{PARQUET_DIR}}/league_results_merged.parquet');

CREATE OR REPLACE TABLE tournament_line AS
SELECT
    NULLIF(TRIM(CAST("Season" AS VARCHAR)), '') AS season,
    TRY_CAST(NULLIF(TRIM(CAST("Date" AS VARCHAR)), '') AS DATE) AS game_date,
    NULLIF(TRIM(CAST("Location" AS VARCHAR)), '') AS location,
    lower(NULLIF(TRIM(CAST("Event Type" AS VARCHAR)), '')) AS event_type,
    TRY_CAST(NULLIF(TRIM(CAST("Round Number" AS VARCHAR)), '') AS INTEGER) AS round_number,
    NULLIF(TRIM(CAST("Round Name" AS VARCHAR)), '') AS round_name,
    NULLIF(TRIM(CAST("Player" AS VARCHAR)), '') AS player_name,
    NULLIF(TRIM(CAST("Player ID" AS VARCHAR)), '') AS player_id,
    NULLIF(TRIM(CAST("Club" AS VARCHAR)), '') AS club,
    TRY_CAST(NULLIF(TRIM(CAST("Game Number" AS VARCHAR)), '') AS INTEGER) AS game_number,
    TRY_CAST(NULLIF(TRIM(CAST("Score" AS VARCHAR)), '') AS INTEGER) AS score,
    TRY_CAST(NULLIF(TRIM(CAST("Handicap" AS VARCHAR)), '') AS DOUBLE) AS handicap,
    TRY_CAST(NULLIF(TRIM(CAST("A Priori Average" AS VARCHAR)), '') AS DOUBLE) AS apriori_average,
    TRY_CAST(NULLIF(TRIM(CAST("Handicap Reference" AS VARCHAR)), '') AS DOUBLE) AS handicap_reference,
    TRY_CAST(NULLIF(TRIM(CAST("Cumulative Score" AS VARCHAR)), '') AS INTEGER) AS cumulative_score,
    TRY_CAST(NULLIF(TRIM(CAST("Stage Rank" AS VARCHAR)), '') AS INTEGER) AS stage_rank,
    TRY_CAST(NULLIF(TRIM(CAST("Cut Line" AS VARCHAR)), '') AS INTEGER) AS cut_line,
    NULLIF(TRIM(CAST("Cut Basis" AS VARCHAR)), '') AS cut_basis,
    TRY_CAST(NULLIF(TRIM(CAST("Overall Cumulative Score" AS VARCHAR)), '') AS INTEGER) AS overall_cumulative_score,
    NULLIF(TRIM(CAST("Verein" AS VARCHAR)), '') AS verein,
    NULLIF(TRIM(CAST("History Club" AS VARCHAR)), '') AS history_club,
    NULLIF(TRIM(CAST("Affiliation Source" AS VARCHAR)), '') AS affiliation_source,
    NULLIF(TRIM(CAST("Event" AS VARCHAR)), '') AS event,
    CASE lower(TRIM(CAST("Input Data" AS VARCHAR)))
        WHEN 'true' THEN TRUE
        WHEN '1' THEN TRUE
        WHEN 'yes' THEN TRUE
        WHEN 'false' THEN FALSE
        WHEN '0' THEN FALSE
        WHEN 'no' THEN FALSE
        ELSE NULL
    END AS input_data,
    CASE lower(TRIM(CAST("Computed Data" AS VARCHAR)))
        WHEN 'true' THEN TRUE
        WHEN '1' THEN TRUE
        WHEN 'yes' THEN TRUE
        WHEN 'false' THEN FALSE
        WHEN '0' THEN FALSE
        WHEN 'no' THEN FALSE
        ELSE NULL
    END AS computed_data
FROM read_parquet('{{PARQUET_DIR}}/tournaments_postprocessed.parquet');

CREATE INDEX IF NOT EXISTS idx_game_line_scope
    ON game_line (season, event, week, team, player_id);

CREATE INDEX IF NOT EXISTS idx_tournament_line_scope
    ON tournament_line (season, event, player_id, round_number, game_number);
