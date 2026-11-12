
-- This script creates a table named 'base_universe' that contains every voter
-- for whom there is a governor persuasion score, governor support score,
-- and turnout score, along with a DNC support score.
--
-- This base can then be used to create targeted voter universes using
-- these scores and voters' geographic and demographic information.
--
-- Each row represents a voter, and each column contains information
-- about that voter.
--
-- Additional comments explain each element of the script.


CREATE OR REPLACE TABLE `project.dataset.base_universe` AS
-- Creates a new table named 'base_universe' in BigQuery.
-- If the table already exists, it will be replaced with the new results.


WITH clarity_deduped AS (
-- Creates a temporary sub-table (CTE) to eliminate duplicate voters
-- from the governor persuasion and support scores table.

  SELECT *
  FROM `project.dataset.governor_scores`

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY person_id
    ORDER BY person_id
  ) = 1
  -- Assigns a row number to each record within each person_id group.
  -- Keeps only one record per person_id, eliminating duplicates.
),

turnout_deduped AS (
-- Creates another temporary sub-table to eliminate duplicate voters
-- from the turnout scores table.

  SELECT *
  FROM `project.dataset.turnout_scores`

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY person_id
    ORDER BY person_id
  ) = 1
  -- Keeps only one turnout score record per voter.
),

dnc_score_deduped AS (
-- Creates a third temporary sub-table to eliminate duplicate voters
-- from the DNC support scores table.

  SELECT *
  FROM `project.dataset.dnc_scores`

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY person_id
    ORDER BY person_id
  ) = 1
  -- Keeps only one DNC support score record per voter.
)


SELECT
-- Identifies the columns from the existing tables that we want
-- to include in our base universe.

    p.person_id,
    -- 'p.' refers to the voter file table ('project.dataset.person').
    -- 'person_id' is the unique identifier used to join voter records.

    p.myv_van_id,
    -- The voter's VAN ID, used to identify voters in VAN/VoteBuilder.

    p.first_name,
    p.last_name,
    p.age_combined,
    p.gender_combined,
    p.date_of_birth_combined,
    p.ethnicity_vf,
    p.ethnicity_modeled,
    p.ethnicity_modeled_confidence_level,
    p.ethnicity_combined,
    -- Demographic information associated with each voter.

    p.county_name,
    p.voting_address_id,
    p.voting_street_address,
    p.voting_city,
    p.voting_zip,
    p.mailing_street_address,
    p.mailing_city,
    p.mailing_zip,
    p.mailing_state,
    -- Geographic and address information associated with each voter.

    p.precinct_name,
    p.van_precinct_id,
    p.van_precinct_name,
    p.us_cong_district_latest,
    p.state_senate_district_latest,
    p.state_house_district_latest,
    -- Electoral district and precinct information for each voter.

    p.is_deceased,
    p.primary_phone_number,
    p.primary_cell_number,
    p.primary_landline_number,
    -- Additional voter status and contact information.

    c.persuasion_score_v1,
    -- 'c.' refers to the governor persuasion and support scores table.
    -- This score estimates how persuadable a voter may be.

    c.support_score_v1,
    -- The governor support score associated with each voter.

    t.clarity_2026_turnout_v2_1 * 100
      AS clarity_2026_turnout_v2_1,
    -- 't.' refers to the turnout scores table.
    -- Multiplies the turnout score by 100 to express it on a
    -- 0-100 scale, assuming the original score is between 0 and 1.

    d.support * 100 AS dnc_support_score
    -- 'd.' refers to the DNC support scores table.
    -- Multiplies the support score by 100 to express it on a
    -- 0-100 scale, assuming the original score is between 0 and 1.


FROM `project.dataset.person` p
-- Identifies the voter file as the primary source of voter information.
-- The alias 'p' is used to reference columns from this table.


INNER JOIN clarity_deduped c
-- Connects the voter file to the deduplicated governor scores table.
-- INNER JOIN ensures only voters appearing in both tables are included.

  ON p.person_id = c.person_id
  -- Matches records using the unique person_id.

  AND c.support_score_v1 IS NOT NULL
  -- Excludes voters who do not have a governor support score.

  AND c.persuasion_score_v1 IS NOT NULL
  -- Excludes voters who do not have a governor persuasion score.


INNER JOIN turnout_deduped t
-- Connects the voter file to the deduplicated turnout scores table.
-- Only voters with matching turnout records are retained.

  ON p.person_id = t.person_id
  -- Matches turnout records to voters using person_id.

  AND t.clarity_2026_turnout_v2_1 IS NOT NULL
  -- Excludes voters who do not have a turnout score.


INNER JOIN dnc_score_deduped d
-- Connects the voter file to the deduplicated DNC support scores table.
-- Only voters appearing in this table are retained.

  ON p.person_id = d.person_id
  -- Matches DNC support scores to voters using person_id.

;
-- Ends the query and creates the final base_universe table.
