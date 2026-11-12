
-- This script creates a table named 'non_nyc_universe' that contains
-- voters outside of New York City who meet specific targeting criteria
-- based on their turnout, support, and persuasion scores.
--
-- The script creates two separate groups of voters:
--   1. Voters with moderate turnout scores and moderate support scores.
--   2. Voters with high turnout scores and higher persuasion scores.
--
-- These two groups are combined into one universe, after which
-- geographic, score-based, and survey response exclusions are applied.
--
-- Each row represents a voter, and each column contains demographic,
-- geographic, contact, or predictive scoring information.
--
-- Additional comments explain each element of the script.


CREATE OR REPLACE TABLE `project.dataset.non_nyc_universe` AS
-- Creates a new table named 'non_nyc_universe' in BigQuery.
-- If the table already exists, it will be replaced with the new results.


WITH step1 AS (
-- Creates a temporary sub-table (CTE) containing voters who have
-- moderate turnout scores and moderate governor support scores.
--
-- This group identifies voters who may be worth contacting
-- because they are neither extremely unlikely nor extremely
-- likely to vote, and their support is not firmly established.

  SELECT *
  FROM `project.dataset.base_universe`
  -- Selects all voter information and predictive scores
  -- from the previously created base universe.

  WHERE clarity_2026_turnout_v2_1 >= 55
    -- Includes voters with a turnout score of at least 55.

    AND clarity_2026_turnout_v2_1 < 75
    -- Excludes voters with a turnout score of 75 or higher.
    -- Together, these conditions select turnout scores
    -- greater than or equal to 55 and less than 75.

    AND support_score_v1 >= 40
    -- Includes voters with a governor support score
    -- of at least 40.

    AND support_score_v1 <= 70
    -- Excludes voters with a governor support score above 70.
    -- Together, these conditions select support scores
    -- between 40 and 70, inclusive.
),


step2 AS (
-- Creates a second temporary sub-table containing voters
-- with high turnout scores and relatively high persuasion scores.
--
-- This group identifies voters who are highly likely to vote
-- and meet a minimum persuasion score threshold.

  SELECT *
  FROM `project.dataset.base_universe`
  -- Uses the same base universe as the first targeting group.

  WHERE clarity_2026_turnout_v2_1 >= 80
    -- Includes voters with a turnout score of 80 or higher.

    AND persuasion_score_v1 >= 40
    -- Includes voters with a persuasion score of at least 40.
    -- Both conditions must be satisfied for a voter
    -- to be included in this group.
),


combined AS (
-- Creates a third temporary sub-table that combines
-- the two targeting groups into one larger universe.

  SELECT *
  FROM step1
  -- Selects all voters from the moderate-turnout,
  -- moderate-support group.

  UNION ALL

  SELECT *
  FROM step2
  -- Adds all voters from the high-turnout,
  -- higher-persuasion group.
  --
  -- UNION ALL combines records without removing duplicates.
  -- Because both groups come from the same base universe,
  -- they have the same column structure.
  --
  -- In this case, the turnout ranges do not overlap,
  -- so an individual record cannot qualify for both groups.
)


SELECT c.*
-- Selects every column from the combined targeting universe.
-- 'c.' refers to the combined sub-table.


FROM combined c
-- Uses the combined targeting universe as the starting point
-- for applying the final exclusion criteria.


WHERE c.county_name NOT IN (
    'Kings',
    'Bronx',
    'New York',
    'Queens'
)
-- Excludes voters living in the four listed NYC counties.
-- These correspond to Brooklyn, the Bronx, Manhattan, and Queens.
--
-- Staten Island (Richmond County) is not excluded by this condition.


AND c.us_cong_district_latest NOT IN (
    '001',
    '004',
    '017',
    '019',
    '021'
)
-- Excludes voters living in the specified congressional districts.
--
-- Congressional districts are identified using their
-- three-digit district codes.
--
-- Voters in any of these districts are removed regardless
-- of whether they meet the score-based targeting criteria.


AND c.voting_city NOT IN (
    'Buffalo',
    'Rochester'
)
-- Excludes voters whose voting city is Buffalo or Rochester.
--
-- This allows these cities to be targeted separately
-- through other voter universes.


AND NOT (
    IFNULL(c.support_score_v1, 0) >= 90
    AND IFNULL(c.clarity_2026_turnout_v2_1, 0) >= 90
)
-- Removes voters who have BOTH:
--   1. A governor support score of 90 or higher.
--   2. A turnout score of 90 or higher.
--
-- These voters are considered highly likely to support
-- the candidate and highly likely to vote.
--
-- IFNULL replaces missing score values with 0,
-- preventing null values from producing an unknown result.
--
-- A voter is excluded only if BOTH thresholds are met.
-- Voters meeting only one threshold remain eligible
-- under this condition.


AND NOT EXISTS (
-- Removes voters who have previously indicated opposition
-- to the candidate in a survey response.

    SELECT 1
    FROM `project.dataset.survey_results` s
    -- 's' refers to the survey results table.

    WHERE s.myv_van_id = c.myv_van_id
    -- Matches survey responses to voters using their VAN ID.

      AND s.survey_response_name IN (
          '5 - Strong Oppose',
          '4 - Lean Oppose'
      )
      -- Identifies voters who indicated that they
      -- strongly oppose or lean toward opposing the candidate.
)
-- NOT EXISTS ensures that voters with either of these
-- survey responses are excluded from the final universe.


;
-- Ends the query and creates the final non_nyc_universe table.
--
-- The resulting table contains voters from the two targeting
-- groups who do not meet any of the geographic, score-based,
-- or survey response exclusion criteria.
