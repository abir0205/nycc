
-- This script creates a table named 'nyc_universe' that contains
-- voters in New York City, Buffalo, and Rochester who meet specific
-- targeting criteria based on their turnout and governor support scores.
--
-- The script identifies voters who have relatively high support
-- scores but lower or moderate turnout scores.
--
-- These voters may be strong supporters who could benefit from
-- additional outreach to encourage them to participate in the election.
--
-- Geographic, score-based, and survey response exclusions are
-- then applied to create the final targeting universe.
--
-- Each row represents a voter, and each column contains demographic,
-- geographic, contact, or predictive scoring information.
--
-- Additional comments explain each element of the script.


CREATE OR REPLACE TABLE `project.dataset.nyc_universe` AS
-- Creates a new table named 'nyc_universe' in BigQuery.
-- If the table already exists, it will be replaced with the new results.


SELECT b.*
-- Selects every column from the base universe.
-- 'b.' refers to the base universe table.


FROM `project.dataset.base_universe` b
-- Uses the previously created base universe as the source
-- of voter information and predictive scores.


WHERE b.clarity_2026_turnout_v2_1 >= 30
-- Includes voters with a turnout score of at least 30.


  AND b.clarity_2026_turnout_v2_1 <= 70
  -- Excludes voters with a turnout score above 70.
  --
  -- Together, these conditions select voters with
  -- turnout scores between 30 and 70, inclusive.


  AND b.support_score_v1 >= 80
  -- Includes voters with a governor support score of at least 80.
  --
  -- These voters are modeled as relatively likely to support
  -- the candidate but may not be highly likely to vote.
  --
  -- Both the turnout and support score requirements
  -- must be satisfied for a voter to qualify.


  AND (
      b.county_name IN (
          'Kings',
          'Bronx',
          'New York',
          'Queens',
          'Richmond'
      )
      -- Includes voters living in any of New York City's
      -- five counties:
      --   Kings    = Brooklyn
      --   Bronx    = The Bronx
      --   New York = Manhattan
      --   Queens   = Queens
      --   Richmond = Staten Island

      OR b.voting_city IN (
          'Buffalo',
          'Rochester'
      )
      -- Also includes voters whose voting city is
      -- Buffalo or Rochester.
      --
      -- OR means that a voter only needs to meet one
      -- of these geographic conditions to qualify.
      --
      -- This allows the same universe to target voters
      -- in NYC, Buffalo, and Rochester.
  )


  AND NOT (
      IFNULL(b.support_score_v1, 0) >= 90
      AND IFNULL(b.clarity_2026_turnout_v2_1, 0) >= 90
  )
  -- Removes voters who have BOTH:
  --   1. A governor support score of 90 or higher.
  --   2. A turnout score of 90 or higher.
  --
  -- These voters are considered highly likely to support
  -- the candidate and highly likely to vote.
  --
  -- IFNULL replaces missing scores with 0 so that null
  -- values do not produce an unknown comparison result.
  --
  -- Both thresholds must be met for a voter to be excluded.
  --
  -- Because this universe already requires turnout scores
  -- of 70 or lower, no qualifying voter can meet the
  -- turnout threshold of 90 in this exclusion.
  --
  -- The condition is therefore redundant under the
  -- current targeting criteria, but is preserved here
  -- to maintain the original query's logic.


  AND NOT EXISTS (
  -- Removes voters who have previously indicated opposition
  -- to the candidate in a survey response.

      SELECT 1
      FROM `project.dataset.survey_results` s
      -- 's' refers to the survey results table.

      WHERE s.myv_van_id = b.myv_van_id
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
-- Ends the query and creates the final nyc_universe table.
--
-- The resulting table contains voters in NYC, Buffalo, and Rochester
-- who have turnout scores between 30 and 70 and support scores
-- of at least 80, excluding those with matching opposition
-- survey responses.
