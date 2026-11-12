
-- This script creates six separate voter targeting universes,
-- each corresponding to a specific congressional district.
--
-- The universes are created using a combination of:
--   1. Congressional district identifiers.
--   2. DCCC candidate support model scores.
--   3. Governor support scores.
--   4. Modeled voter turnout scores.
--
-- Four districts use DCCC candidate support scores:
-- NY-01, NY-04, NY-17, and NY-19.
--
-- Two districts use governor support scores:
-- NY-18 and NY-21.
--
-- Each resulting table contains voters who meet the targeting
-- criteria for that congressional district.
--
-- Each row represents a voter, and each column contains
-- demographic, geographic, contact, or predictive scoring information.
--
-- Additional comments explain each element of the script.


-- ============================================================
-- CONGRESSIONAL DISTRICT 1 (NY-01)
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ny_01_scores` AS
-- Creates a new table containing voters targeted for NY-01.
-- If the table already exists, it will be replaced.


SELECT b.*
-- Selects every column from the base universe.
-- 'b.' refers to the base universe table.


FROM `project.dataset.base_universe` b
-- Uses the previously created base universe as the primary
-- source of voter information and predictive scores.


INNER JOIN `project.dataset.dccc_support_scores` s
-- Connects the base universe to the DCCC candidate support
-- model scores table.
--
-- 's.' refers to the DCCC scores table.
-- INNER JOIN ensures that only voters appearing in both
-- tables are included in the resulting universe.

  ON b.person_id = s.person_id
  -- Matches voters between the two tables using person_id.


WHERE s.cd = 'NY-01'
-- Includes only voters assigned to congressional district NY-01
-- in the DCCC scores table.


  AND s.dccc_csm_score >= 75
  -- Includes voters with a DCCC candidate support score
  -- of at least 75.


  AND s.dccc_csm_score <= 90
  -- Excludes voters with a DCCC candidate support score above 90.
  --
  -- Together, these conditions select support scores
  -- between 75 and 90, inclusive.


  AND b.clarity_2026_turnout_v2_1 BETWEEN 30 AND 65
  -- Includes voters with modeled turnout scores between
  -- 30 and 65, inclusive.
  --
  -- BETWEEN includes both the lower and upper bounds.
  --
  -- This targets voters with relatively high modeled
  -- candidate support but lower or moderate turnout likelihood.


;
-- Ends the query and creates the NY-01 targeting universe.



-- ============================================================
-- CONGRESSIONAL DISTRICT 4 (NY-04)
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ny_04_scores` AS
-- Creates a new table containing voters targeted for NY-04.
-- If the table already exists, it will be replaced.


SELECT b.*
-- Selects all voter information and predictive scores
-- from the base universe.


FROM `project.dataset.base_universe` b
-- Uses the base universe as the primary source of voter records.


INNER JOIN `project.dataset.dccc_support_scores` s
-- Connects the voter file to the DCCC candidate support
-- model scores table.

  ON b.person_id = s.person_id
  -- Matches voters using their unique person_id.


WHERE s.cd = 'NY-04'
-- Includes only voters assigned to congressional district NY-04
-- in the DCCC scores table.


  AND s.dccc_csm_score >= 75
  -- Includes voters with a DCCC candidate support score
  -- of at least 75.


  AND s.dccc_csm_score <= 90
  -- Excludes voters with a DCCC candidate support score above 90.
  --
  -- Together, these conditions select support scores
  -- between 75 and 90, inclusive.


  AND b.clarity_2026_turnout_v2_1 BETWEEN 30 AND 65
  -- Includes voters with modeled turnout scores
  -- between 30 and 65, inclusive.
  --
  -- This identifies voters with relatively high candidate
  -- support scores who may benefit from turnout outreach.


;
-- Ends the query and creates the NY-04 targeting universe.



-- ============================================================
-- CONGRESSIONAL DISTRICT 17 (NY-17)
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ny_17_scores` AS
-- Creates a new table containing voters targeted for NY-17.
-- If the table already exists, it will be replaced.


SELECT b.*
-- Selects all columns from the base universe.


FROM `project.dataset.base_universe` b
-- Uses the base universe as the primary source
-- of voter information and predictive scores.


INNER JOIN `project.dataset.dccc_support_scores` s
-- Connects the base universe to the DCCC candidate support
-- model scores table.

  ON b.person_id = s.person_id
  -- Matches records using person_id.


WHERE s.cd = 'NY-17'
-- Includes only voters assigned to congressional district NY-17
-- in the DCCC scores table.


  AND s.dccc_csm_score >= 75
  -- Includes voters with a DCCC candidate support score
  -- of at least 75.


  AND s.dccc_csm_score <= 90
  -- Excludes voters with a DCCC candidate support score above 90.
  --
  -- Together, these conditions select support scores
  -- between 75 and 90, inclusive.


  AND b.clarity_2026_turnout_v2_1 BETWEEN 30 AND 65
  -- Includes voters with modeled turnout scores
  -- between 30 and 65, inclusive.
  --
  -- This targets voters with relatively high modeled
  -- candidate support but lower or moderate turnout likelihood.


;
-- Ends the query and creates the NY-17 targeting universe.



-- ============================================================
-- CONGRESSIONAL DISTRICT 18 (NY-18)
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ny_18_scores` AS
-- Creates a new table containing voters targeted for NY-18.
-- If the table already exists, it will be replaced.
--
-- Unlike the previous districts, this universe uses
-- governor support scores rather than DCCC support scores.


SELECT b.*
-- Selects every column from the base universe.


FROM `project.dataset.base_universe` b
-- Uses the base universe as the primary source
-- of voter information and predictive scores.


INNER JOIN `project.dataset.governor_support_scores` s
-- Connects the base universe to the governor support scores table.
--
-- 's.' refers to the governor support scores table.
-- INNER JOIN retains only voters appearing in both tables.

  ON b.person_id = s.person_id
  -- Matches voter records using person_id.


WHERE b.us_cong_district_latest = '018'
-- Includes only voters whose latest congressional district
-- assignment is district 18.
--
-- Unlike the DCCC-based queries, this condition uses
-- the district assignment stored in the base universe.


  AND s.support_score_v1 >= 40
  -- Includes voters with a governor support score
  -- of at least 40.


  AND s.support_score_v1 <= 80
  -- Excludes voters with a governor support score above 80.
  --
  -- Together, these conditions select governor support
  -- scores between 40 and 80, inclusive.
  --
  -- This range includes voters with moderate support scores
  -- who may be suitable for persuasion outreach.


  AND b.clarity_2026_turnout_v2_1 BETWEEN 30 AND 80
  -- Includes voters with modeled turnout scores
  -- between 30 and 80, inclusive.
  --
  -- This is a broader turnout range than the DCCC-based
  -- universes, which use turnout scores between 30 and 65.


;
-- Ends the query and creates the NY-18 targeting universe.



-- ============================================================
-- CONGRESSIONAL DISTRICT 19 (NY-19)
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ny_19_scores` AS
-- Creates a new table containing voters targeted for NY-19.
-- If the table already exists, it will be replaced.


SELECT b.*
-- Selects all voter information and predictive scores
-- from the base universe.


FROM `project.dataset.base_universe` b
-- Uses the base universe as the primary source of voter records.


INNER JOIN `project.dataset.dccc_support_scores` s
-- Connects the base universe to the DCCC candidate support
-- model scores table.

  ON b.person_id = s.person_id
  -- Matches voters using person_id.


WHERE s.cd = 'NY-19'
-- Includes only voters assigned to congressional district NY-19
-- in the DCCC scores table.


  AND s.dccc_csm_score >= 75
  -- Includes voters with a DCCC candidate support score
  -- of at least 75.


  AND s.dccc_csm_score <= 90
  -- Excludes voters with a DCCC candidate support score above 90.
  --
  -- Together, these conditions select support scores
  -- between 75 and 90, inclusive.


  AND b.clarity_2026_turnout_v2_1 BETWEEN 30 AND 65
  -- Includes voters with modeled turnout scores
  -- between 30 and 65, inclusive.
  --
  -- This identifies voters with relatively high modeled
  -- candidate support and lower or moderate turnout likelihood.


;
-- Ends the query and creates the NY-19 targeting universe.



-- ============================================================
-- CONGRESSIONAL DISTRICT 21 (NY-21)
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ny_21_scores` AS
-- Creates a new table containing voters targeted for NY-21.
-- If the table already exists, it will be replaced.
--
-- Like NY-18, this universe uses governor support scores
-- rather than DCCC candidate support scores.


SELECT b.*
-- Selects every column from the base universe.


FROM `project.dataset.base_universe` b
-- Uses the base universe as the primary source
-- of voter information and predictive scores.


INNER JOIN `project.dataset.governor_support_scores` s
-- Connects the base universe to the governor support scores table.

  ON b.person_id = s.person_id
  -- Matches voter records using person_id.


WHERE b.us_cong_district_latest = '021'
-- Includes only voters whose latest congressional district
-- assignment is district 21.
--
-- This condition uses the district assignment stored
-- in the base universe.


  AND s.support_score_v1 >= 40
  -- Includes voters with a governor support score
  -- of at least 40.


  AND s.support_score_v1 <= 80
  -- Excludes voters with a governor support score above 80.
  --
  -- Together, these conditions select governor support
  -- scores between 40 and 80, inclusive.


  AND b.clarity_2026_turnout_v2_1 BETWEEN 30 AND 80
  -- Includes voters with modeled turnout scores
  -- between 30 and 80, inclusive.
  --
  -- This targets voters with moderate governor support
  -- scores across a relatively broad turnout range.


;
-- Ends the query and creates the NY-21 targeting universe.
--
-- All six congressional district universes have now been created.
-- These tables can be used independently or combined with
-- other targeting universes in a subsequent query.
