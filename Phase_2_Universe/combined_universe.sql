
-- This script creates a table named 'combined_universe' that combines
-- several voter contact universes into one master universe.
--
-- It combines voters from different geographic and targeting groups,
-- eliminates duplicate records, and removes voters who meet certain
-- exclusion criteria based on their contact history, survey responses,
-- and predictive scores.
--
-- Each row represents a unique voter, and each column contains
-- demographic, geographic, contact, or predictive scoring information.
--
-- Additional comments explain each element of the script.


CREATE OR REPLACE TABLE `project.dataset.combined_universe` AS
-- Creates a new table named 'combined_universe' in BigQuery.
-- If the table already exists, it will be replaced with the new results.


WITH unioned AS (
-- Creates a temporary sub-table (CTE) that combines several
-- independently created voter universes into one larger dataset.

  SELECT
      person_id,
      myv_van_id,
      first_name,
      last_name,
      age_combined,
      gender_combined,
      date_of_birth_combined,
      ethnicity_vf,
      ethnicity_modeled,
      ethnicity_modeled_confidence_level,
      ethnicity_combined,
      county_name,
      voting_address_id,
      voting_street_address,
      voting_city,
      voting_zip,
      mailing_street_address,
      mailing_city,
      mailing_zip,
      mailing_state,
      precinct_name,
      van_precinct_id,
      van_precinct_name,
      us_cong_district_latest,
      state_senate_district_latest,
      state_house_district_latest,
      is_deceased,
      primary_phone_number,
      primary_cell_number,
      primary_landline_number,
      persuasion_score_v1,
      support_score_v1,
      clarity_2026_turnout_v2_1,
      dnc_support_score

  FROM `project.dataset.target_universe_1`
  -- Selects the voter information and predictive scores from
  -- the first targeting universe.
  -- These columns establish the structure of the combined table.


  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_2`
  -- Adds all voters from the second targeting universe.
  -- UNION ALL combines records without removing duplicates.
  -- BY NAME matches columns based on their names rather than
  -- their positions in the SELECT statements.


  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_3`
  -- Adds all voters from the third targeting universe.


  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_4`
  -- Adds all voters from the fourth targeting universe.


  -- Additional targeting universes can be included if needed.
  -- These are currently commented out and will not be executed.

  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_5`

  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_6`

  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_7`

  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_8`

  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_9`

  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_10`


  UNION ALL BY NAME
  SELECT * FROM `project.dataset.target_universe_11`
  -- Adds all voters from the final targeting universe.
  -- At this stage, a voter may appear multiple times if they
  -- were included in more than one source universe.
),


deduped AS (
-- Creates another temporary sub-table to eliminate duplicate voters
-- from the combined dataset.

  SELECT *
  FROM unioned

  QUALIFY ROW_NUMBER() OVER (
      PARTITION BY person_id
      ORDER BY CASE
          WHEN support_score_v1 IS NOT NULL THEN 0
          ELSE 1
      END
  ) = 1

  -- PARTITION BY groups records belonging to the same voter
  -- using their unique person_id.
  --
  -- ROW_NUMBER assigns a number to each record within that group.
  --
  -- The CASE statement prioritizes records that contain
  -- a non-null governor support score.
  --
  -- Records with a support score receive a priority of 0,
  -- while records without a support score receive a priority of 1.
  --
  -- QUALIFY keeps only the highest-priority record for each voter,
  -- ensuring that each person_id appears only once.
)


SELECT d.*
-- Selects every column from the deduplicated universe.
-- 'd.' refers to the deduped sub-table.


FROM deduped d
-- Uses the deduplicated voter universe as the starting point
-- for applying the final exclusion criteria.


WHERE NOT EXISTS (
-- Removes voters who have certain negative contact results
-- recorded in their voter contact history.

    SELECT 1
    FROM `project.dataset.voter_contacts` cm2
    -- 'cm2' refers to the voter contact history table.

    WHERE cm2.person_id = d.person_id
    -- Matches contact records to voters using person_id.

      AND cm2.result_name IN (
          'Refused',
          'Do Not Call',
          'Disconnected',
          'Hostile',
          'Refused Contact',
          'Threatened'
      )
      -- Identifies voters with any of the listed negative
      -- contact results.

      AND cm2.datetime_canvassed >= TIMESTAMP('2025-06-01')
      -- Only considers contact results recorded on or after
      -- June 1, 2025.
)
-- NOT EXISTS ensures that voters with at least one matching
-- negative contact record are excluded from the final universe.


AND NOT EXISTS (
-- Removes voters who have recently been canvassed.

    SELECT 1
    FROM `project.dataset.voter_contacts` cm3
    -- Uses the same contact history table to identify
    -- recent canvassing activity.

    WHERE cm3.person_id = d.person_id
    -- Matches contact records to voters using person_id.

      AND cm3.result_name = 'Canvassed'
      -- Identifies records with a result of 'Canvassed'.

      AND cm3.datetime_canvassed >= TIMESTAMP_SUB(
          CURRENT_TIMESTAMP(),
          INTERVAL 3 DAY
      )
      -- Only considers canvassing activity within the last
      -- 3 days, calculated relative to the current timestamp.
)
-- NOT EXISTS removes voters who have a matching canvassing
-- record within the past 72 hours.


AND NOT (
    IFNULL(d.support_score_v1, -1) >= 90
    AND IFNULL(d.clarity_2026_turnout_v2_1, -1) >= 90
)
-- Removes voters who have BOTH:
--   1. A governor support score of 90 or higher.
--   2. A turnout score of 90 or higher.
--
-- These voters are considered highly likely to support
-- the candidate and highly likely to vote.
--
-- IFNULL replaces missing scores with -1 so that null values
-- do not cause the comparison to return an unknown result.
--
-- A voter is excluded only when BOTH conditions are satisfied.


AND NOT (
    IFNULL(d.dnc_support_score, -1) >= 90
    AND IFNULL(d.clarity_2026_turnout_v2_1, -1) >= 90
)
-- Removes voters who have BOTH:
--   1. A DNC support score of 90 or higher.
--   2. A turnout score of 90 or higher.
--
-- This provides an additional method of identifying voters
-- who are highly likely to support the candidate and vote.
--
-- As with the previous condition, both score thresholds
-- must be met for a voter to be excluded.


AND NOT EXISTS (
-- Removes voters who have explicitly indicated opposition
-- to the candidate in a previous survey response.

    SELECT 1
    FROM `project.dataset.survey_results` sr
    -- 'sr' refers to the voter survey responses table.

    WHERE sr.myv_van_id = d.myv_van_id
    -- Matches survey responses to voters using their VAN ID.

      AND sr.survey_response_name IN (
          '4 - Lean Oppose',
          '5 - Strong Oppose'
      )
      -- Identifies voters who indicated that they either
      -- lean toward opposing or strongly oppose the candidate.
)
-- NOT EXISTS ensures that voters with either of these
-- survey responses are excluded from the final universe.


;
-- Ends the query and creates the final combined_universe table.
-- The resulting table contains deduplicated voters from all
-- included targeting universes who do not meet any of the
-- specified exclusion criteria.
