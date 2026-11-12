
-- ============================================================
-- VOLUNTEER PROPENSITY MODEL: VOTER HISTORY FEATURE ENGINEERING
-- ============================================================
--
-- This script creates 'ml_features_table_v4' by adding historical
-- voting behavior features to the existing 'ml_features_table_v3'.
--
-- These features are used as predictors in a machine learning model
-- designed to estimate a voter's propensity to volunteer.
--
-- Historical voting behavior provides information about a voter's
-- civic participation, engagement, and consistency over time.
--
-- The script creates seven additional features:
--
-- 1. general_votes_last3:
--    Number of even-year general elections voted in (2020-2024).
--
-- 2. offyear_votes_last3:
--    Number of odd-year elections voted in (2021-2025).
--
-- 3. voted_all_recent_generals:
--    Whether the voter participated in all three recent
--    even-year general elections.
--
-- 4. longest_gap_years:
--    Longest interval between consecutive recorded general
--    election participations from 2014 through 2025.
--
-- 5. primary_dem_votes_last5:
--    Number of Democratic primaries voted in from 2020-2024.
--
-- 6. voted_early_last_general:
--    Whether the voter voted early in the 2025 general election.
--
-- 7. voted_absentee_last_general:
--    Whether the voter voted absentee in the 2025 general election.
--
-- The resulting table retains all existing features from v3
-- and appends these seven additional voter history features.
--
-- ============================================================


CREATE OR REPLACE TABLE `project.dataset.ml_features_table_v4` AS
-- Creates a new BigQuery table containing the expanded feature set.
-- If the table already exists, it will be replaced.


WITH general_unpivoted AS (
-- Creates a temporary sub-table containing general election
-- participation records from 2014 through 2025.
--
-- The original voter history table stores election participation
-- in separate columns, with one column for each election year.
--
-- UNPIVOT transforms these columns into rows, allowing voting
-- history to be analyzed across election years.

  SELECT
    person_id,
    -- Unique voter identifier used to join voting history
    -- to the machine learning feature table.

    CAST(REGEXP_EXTRACT(election_col, r'\d+') AS INT64)
      AS election_year,
    -- Extracts the election year from the original column name.
    --
    -- Example:
    -- vote_g_2024 becomes 2024.
    --
    -- REGEXP_EXTRACT extracts the numeric portion of the name,
    -- and CAST converts the extracted value into an integer.

    voted
    -- Indicates whether the voter participated in the election.
    -- The following aggregations assume 1 = voted and 0 = did not vote.

  FROM `democrats.analytics_ny.person_votes`
  -- Source table containing historical voter participation records.

  UNPIVOT (
    voted FOR election_col IN (
      vote_g_2014,
      vote_g_2015,
      vote_g_2016,
      vote_g_2017,
      vote_g_2018,
      vote_g_2019,
      vote_g_2020,
      vote_g_2021,
      vote_g_2022,
      vote_g_2023,
      vote_g_2024,
      vote_g_2025
    )
  )
  -- Converts the twelve election-year columns into
  -- individual records containing:
  -- person_id, election_year, and voted.

  WHERE state_code = 'NY'
  -- Restricts the voting history records to New York State.
),


primary_unpivoted AS (
-- Creates another temporary sub-table containing Democratic
-- primary election participation records from 2020 through 2024.
--
-- Primary participation can provide an additional measure
-- of political engagement beyond general election turnout.

  SELECT
    person_id,

    CAST(REGEXP_EXTRACT(election_col, r'\d+') AS INT64)
      AS election_year,
    -- Extracts the election year from the primary election
    -- column name.

    voted
    -- Indicates whether the voter participated in that
    -- Democratic primary election.

  FROM `democrats.analytics_ny.person_votes`

  UNPIVOT (
    voted FOR election_col IN (
      vote_p_2020_party_d,
      vote_p_2021_party_d,
      vote_p_2022_party_d,
      vote_p_2023_party_d,
      vote_p_2024_party_d
    )
  )
  -- Converts five Democratic primary participation columns
  -- into individual records for each voter and election year.

  WHERE state_code = 'NY'
  -- Restricts the records to New York State.
),


-- ============================================================
-- GENERAL AND OFF-YEAR ELECTION PARTICIPATION FEATURES
-- ============================================================

general_counts AS (
-- Aggregates general election participation records into
-- one row per person_id.
--
-- Separating these calculations from the primary election
-- calculations prevents unnecessary row multiplication
-- when the aggregated features are joined together.

  SELECT
    person_id,

    SUM(
      IF(
        MOD(election_year, 2) = 0
        AND election_year >= 2020,
        voted,
        0
      )
    ) AS general_votes_last3,
    -- FEATURE 1: GENERAL ELECTION PARTICIPATION
    --
    -- Counts the number of even-year general elections
    -- in which a voter participated.
    --
    -- MOD(election_year, 2) = 0 identifies even years.
    --
    -- The selected elections are:
    -- 2020, 2022, and 2024.
    --
    -- Possible values: 0 through 3, assuming one history
    -- record per voter and binary participation indicators.
    --
    -- MODEL RELEVANCE:
    -- Provides a measure of recent general election
    -- participation and voting consistency.


    SUM(
      IF(
        MOD(election_year, 2) = 1
        AND election_year >= 2021,
        voted,
        0
      )
    ) AS offyear_votes_last3
    -- FEATURE 2: OFF-YEAR ELECTION PARTICIPATION
    --
    -- Counts the number of odd-year general elections
    -- in which a voter participated.
    --
    -- MOD(election_year, 2) = 1 identifies odd years.
    --
    -- The selected elections are:
    -- 2021, 2023, and 2025.
    --
    -- Possible values: 0 through 3 under the same assumptions.
    --
    -- MODEL RELEVANCE:
    -- Off-year election participation may capture
    -- engagement beyond higher-profile election cycles.

  FROM general_unpivoted

  GROUP BY person_id
  -- Aggregates voting history into one record per voter.
),


-- ============================================================
-- VOTING CONSISTENCY AND PARTICIPATION GAP FEATURES
-- ============================================================

general_gaps AS (
-- Calculates the longest interval between consecutive
-- recorded general election participations.
--
-- This feature measures the largest gap between elections
-- in which a voter actually participated.
--
-- The calculation uses the available history from 2014-2025.

  SELECT
    person_id,

    MAX(election_year - prev_voted_year)
      AS longest_gap_years
    -- FEATURE 4: LONGEST VOTING GAP
    --
    -- Calculates the maximum number of years between
    -- consecutive recorded election participations.
    --
    -- Example:
    -- A voter participated in 2016, 2020, and 2024.
    --
    -- The gaps are:
    -- 2020 - 2016 = 4 years
    -- 2024 - 2020 = 4 years
    --
    -- longest_gap_years = 4.
    --
    -- MODEL RELEVANCE:
    -- Provides a measure of historical participation
    -- consistency and interruptions in voting behavior.
    --
    -- Voters with fewer than two recorded participations
    -- will have a NULL value for this feature.

  FROM (
    SELECT
      person_id,
      election_year,

      LAG(election_year) OVER (
        PARTITION BY person_id
        ORDER BY election_year
      ) AS prev_voted_year
      -- LAG retrieves the previous election year
      -- in which the voter participated.
      --
      -- PARTITION BY person_id ensures each voter's
      -- history is evaluated independently.
      --
      -- ORDER BY election_year places the voting
      -- records in chronological order.

    FROM general_unpivoted

    WHERE voted = 1
    -- Only considers elections in which the voter
    -- actually participated.
  )

  GROUP BY person_id
  -- Returns one longest-gap value per voter.
),


-- ============================================================
-- DEMOCRATIC PRIMARY PARTICIPATION FEATURES
-- ============================================================

primary_counts AS (
-- Aggregates Democratic primary election participation
-- into one record per voter.

  SELECT
    person_id,

    SUM(voted) AS primary_dem_votes_last5
    -- FEATURE 5: DEMOCRATIC PRIMARY PARTICIPATION
    --
    -- Counts the number of Democratic primary elections
    -- in which the voter participated from 2020-2024.
    --
    -- Possible values: 0 through 5, assuming binary
    -- participation indicators and one record per voter.
    --
    -- MODEL RELEVANCE:
    -- Democratic primary participation may provide
    -- additional information about political engagement
    -- and historical participation in party elections.

  FROM primary_unpivoted

  GROUP BY person_id
  -- Returns one Democratic primary participation count
  -- per voter.
)


-- ============================================================
-- FINAL FEATURE TABLE CREATION
-- ============================================================

SELECT
  v3.*,
  -- Retains every existing feature from ml_features_table_v3.
  --
  -- This allows the model to incorporate the new voting
  -- history features without removing previously
  -- engineered predictors.


  gc.general_votes_last3,
  -- Adds the number of recent even-year general elections
  -- in which the voter participated.


  gc.offyear_votes_last3,
  -- Adds the number of recent odd-year elections
  -- in which the voter participated.


  (gc.general_votes_last3 = 3)
    AS voted_all_recent_generals,
  -- FEATURE 3: CONSISTENT GENERAL ELECTION PARTICIPATION
  --
  -- Creates a Boolean feature indicating whether the voter
  -- participated in all three recent even-year elections:
  -- 2020, 2022, and 2024.
  --
  -- TRUE  = participated in all three elections.
  -- FALSE = participated in fewer than three elections.
  -- NULL  = general_votes_last3 is NULL.
  --
  -- MODEL RELEVANCE:
  -- Provides a simple indicator of consistent recent
  -- general election participation.


  gg.longest_gap_years,
  -- Adds the longest interval between consecutive
  -- recorded general election participations.


  pc.primary_dem_votes_last5,
  -- Adds the number of Democratic primary elections
  -- in which the voter participated from 2020-2024.


  vh.vote_g_2025_method_early
    AS voted_early_last_general,
  -- FEATURE 6: EARLY VOTING HISTORY
  --
  -- Adds the early voting indicator from the
  -- 2025 general election.
  --
  -- MODEL RELEVANCE:
  -- Voting method may capture additional differences
  -- in historical voting behavior and participation habits.


  vh.vote_g_2025_method_absentee
    AS voted_absentee_last_general
  -- FEATURE 7: ABSENTEE VOTING HISTORY
  --
  -- Adds the absentee voting indicator from the
  -- 2025 general election.
  --
  -- MODEL RELEVANCE:
  -- Provides another measure of voting behavior that
  -- the model can evaluate alongside other predictors.


FROM `project.dataset.ml_features_table_v3` v3
-- Uses the previous feature table as the foundation
-- for the expanded machine learning dataset.


LEFT JOIN general_counts gc
  ON v3.person_id = gc.person_id
-- Adds general and off-year election participation counts.
--
-- LEFT JOIN retains voters from v3 even if they
-- do not have matching voting history records.


LEFT JOIN general_gaps gg
  ON v3.person_id = gg.person_id
-- Adds the longest voting gap feature.
--
-- Voters with insufficient voting history may
-- have NULL values for this feature.


LEFT JOIN primary_counts pc
  ON v3.person_id = pc.person_id
-- Adds Democratic primary participation counts.


LEFT JOIN `democrats.analytics_ny.person_votes` vh
  ON v3.person_id = vh.person_id
  AND vh.state_code = 'NY'
-- Connects the feature table to the original
-- voter history table to retrieve 2025 voting method indicators.
--
-- This join assumes that person_votes contains no more than
-- one matching New York record per person_id.
-- Otherwise, the join could duplicate rows.


;
-- Ends the query and creates ml_features_table_v4.
--
-- The resulting dataset contains all existing v3 features
-- plus seven additional voting history features.
--
-- These features can be used during model training,
-- validation, and scoring for the Volunteer Propensity Model.
