
-- ============================================================
-- VOLUNTEER PROPENSITY MODEL:
-- VOLUNTEER RESPONSE LABELS AND PERSON-LEVEL FEATURE ENGINEERING
-- ============================================================
--
-- This script creates 'ml_features_table_v2' by restructuring
-- the original machine learning feature table into a dataset
-- intended to contain one record per voter.
--
-- The primary objective is to create volunteer-related features
-- and labels using historical volunteer survey responses
-- and recorded volunteer shift activity.
--
-- Three specific volunteer survey questions are used:
--
--   1. SQ24 DCCC VOL
--   2. 2024-LG-Vol Ask
--   3. 26 GE Volunteer
--
-- The script identifies each voter's most recent response
-- across these three questions and converts that response
-- into an ordinal volunteer interest score.
--
-- It then creates three volunteer-related columns:
--
--   vol_response_tier:
--     Ordinal score representing the voter's most recent
--     recorded response to a qualifying volunteer question.
--
--   vol_yes:
--     Boolean indicating whether the most recent qualifying
--     volunteer response was "Yes".
--
--   did_volunteer:
--     Boolean indicating whether the voter has at least
--     one recorded volunteer shift.
--
-- The resulting table retains existing voter and model
-- features while removing survey response-level columns.
--
-- ============================================================


CREATE OR REPLACE TABLE `project.dataset.ml_features_table_v2` AS
-- Creates a new BigQuery table containing the restructured
-- volunteer modeling dataset.
--
-- If the table already exists, it will be replaced.


WITH qualifying_responses AS (
-- Creates a temporary sub-table containing only survey responses
-- associated with the three selected volunteer questions.
--
-- This ensures that unrelated survey responses do not affect
-- the volunteer interest labels.

  SELECT *

  FROM `project.dataset.ml_features_table_patched`
  -- Uses the previously prepared feature table containing
  -- survey response information.

  WHERE survey_question_name IN (
    'SQ24 DCCC VOL',
    '2024-LG-Vol Ask',
    '26 GE Volunteer'
  )
  -- Retains only records associated with the three
  -- designated volunteer interest questions.
  --
  -- Responses to all other survey questions are excluded
  -- from the volunteer response classification process.
),


-- ============================================================
-- MOST RECENT VOLUNTEER RESPONSE
-- ============================================================

most_recent_response AS (
-- Identifies the most recent qualifying volunteer survey
-- response for each voter.
--
-- If a voter answered multiple qualifying questions,
-- only the most recent response across all three is retained.
--
-- The most recent response is determined primarily by
-- the actual canvass date rather than the date the
-- database record was modified.

  SELECT *

  FROM qualifying_responses

  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY myc_van_id
    -- Groups all qualifying responses belonging
    -- to the same voter.

    ORDER BY
      date_canvassed DESC NULLS LAST,
      -- Prioritizes the most recent canvass date.
      -- Records with missing canvass dates are ranked last.

      datetime_modified DESC,
      -- Uses the record modification timestamp
      -- to break ties between canvass dates.

      survey_response_id DESC
      -- Uses the survey response ID as an additional
      -- tie-breaker when the previous values match.

  ) = 1
  -- ROW_NUMBER assigns a rank to each qualifying response.
  --
  -- QUALIFY retains only the highest-ranked response
  -- for each myc_van_id.
  --
  -- This produces at most one qualifying volunteer
  -- response record per voter.
),


-- ============================================================
-- VOLUNTEER INTEREST CLASSIFICATION
-- ============================================================

person_vol_features AS (
-- Converts the selected volunteer survey response into
-- a numerical volunteer interest tier.
--
-- The tier represents the most recent recorded response,
-- rather than the strongest response ever recorded.

  SELECT
    myc_van_id,

    CASE

      WHEN LOWER(survey_response_name) = 'yes'
        THEN 4
      -- TIER 4: YES
      --
      -- The voter explicitly indicated willingness
      -- to volunteer.
      --
      -- This is the highest volunteer interest tier.


      WHEN LOWER(survey_response_name) = 'maybe'
        THEN 3
      -- TIER 3: MAYBE
      --
      -- The voter expressed possible interest
      -- in volunteering.


      WHEN LOWER(survey_response_name) LIKE 'later%'
        THEN 2
      -- TIER 2: LATER
      --
      -- The voter indicated that they may be
      -- interested in volunteering at a later time.
      --
      -- LIKE 'later%' matches responses beginning
      -- with the word "later", ignoring case
      -- because LOWER is applied.


      WHEN LOWER(survey_response_name) IN (
        'no',
        'never'
      )
        THEN 1
      -- TIER 1: NO / NEVER
      --
      -- The voter declined to volunteer.
      --
      -- Both "No" and "Never" are assigned
      -- the same response tier.


      ELSE 0
      -- TIER 0: OTHER / UNRECOGNIZED
      --
      -- The response does not match one of the
      -- recognized volunteer interest categories.
      --
      -- This also includes missing response values.

    END AS vol_response_tier
    -- FEATURE: VOLUNTEER RESPONSE TIER
    --
    -- Converts the selected survey response into
    -- an ordinal numerical representation.
    --
    -- Possible values:
    --
    -- 4 = Yes
    -- 3 = Maybe
    -- 2 = Later
    -- 1 = No / Never
    -- 0 = Other / Missing
    --
    -- MODEL RELEVANCE:
    -- Provides a structured representation of
    -- explicitly recorded volunteer interest.
    --
    -- IMPORTANT:
    -- This variable is derived directly from
    -- volunteer survey responses and may introduce
    -- target leakage if used to predict those responses.

  FROM most_recent_response
),


-- ============================================================
-- PERSON-LEVEL VOLUNTEER FEATURES
-- ============================================================

with_vol_columns AS (
-- Adds volunteer response classifications to the original
-- machine learning feature table.
--
-- The classification is joined to all existing records
-- associated with the same myc_van_id.
--
-- Response-level columns are removed because individual
-- survey responses do not necessarily have a single
-- meaningful value at the person level.

  SELECT

    t.* EXCEPT (
      date_canvassed,
      survey_question_id,
      survey_question_name,
      survey_question_text,
      survey_response_id,
      survey_response_name
    ),
    -- Retains all existing columns except the listed
    -- survey response-level fields.
    --
    -- Removing these columns helps eliminate differences
    -- between rows that represent the same voter but
    -- contain different survey response records.


    COALESCE(f.vol_response_tier, 0)
      AS vol_response_tier,
    -- Adds the volunteer response tier to each voter.
    --
    -- COALESCE assigns 0 when no qualifying
    -- volunteer survey response exists.
    --
    -- This ensures that voters without qualifying
    -- responses receive a defined tier value.


    COALESCE(f.vol_response_tier, 0) = 4
      AS vol_yes
    -- FEATURE: VOLUNTEER YES RESPONSE
    --
    -- Creates a Boolean indicator based on
    -- the volunteer response tier.
    --
    -- TRUE  = most recent qualifying response was "Yes".
    -- FALSE = any other response or no qualifying response.
    --
    -- This can serve as a volunteer interest label
    -- for supervised machine learning.
    --
    -- IMPORTANT:
    -- vol_yes represents expressed willingness
    -- to volunteer, not completed volunteer activity.

  FROM `project.dataset.ml_features_table` t
  -- Uses the original machine learning feature table
  -- as the source of existing voter information.


  LEFT JOIN person_vol_features f
    ON t.myc_van_id = f.myc_van_id
  -- Connects the volunteer response classifications
  -- to the original feature table using myc_van_id.
  --
  -- LEFT JOIN retains all original records,
  -- including voters who have no qualifying
  -- volunteer survey response.
  --
  -- Those voters receive vol_response_tier = 0
  -- and vol_yes = FALSE.
)


-- ============================================================
-- FINAL PERSON-LEVEL DATASET
-- ============================================================

SELECT DISTINCT *
FROM with_vol_columns;
-- Removes rows that are identical across all selected columns.
--
-- This helps eliminate duplicate records caused by
-- multiple survey response rows for the same voter.
--
-- IMPORTANT:
-- DISTINCT does not guarantee one row per myc_van_id
-- if other retained columns differ between records.
--
-- The resulting table is written to ml_features_table_v2.



-- ============================================================
-- RECORDED VOLUNTEER PARTICIPATION LABEL
-- ============================================================

CREATE OR REPLACE TABLE `project.dataset.ml_features_table_v2` AS
-- Recreates ml_features_table_v2 using its existing contents
-- and adds a new volunteer participation indicator.
--
-- This is a second CREATE OR REPLACE operation on the same table.


SELECT
  *,
  -- Retains all columns created in the previous step.


  COALESCE(shift_count, 0) > 0
    AS did_volunteer
  -- FEATURE: RECORDED VOLUNTEER PARTICIPATION
  --
  -- Creates a Boolean indicator showing whether
  -- the voter has at least one recorded volunteer shift.
  --
  -- COALESCE replaces missing shift counts with 0.
  --
  -- TRUE  = shift_count is greater than 0.
  -- FALSE = shift_count is 0 or NULL.
  --
  -- MODEL RELEVANCE:
  -- Provides a behavioral indicator of actual recorded
  -- volunteer participation, rather than stated interest.
  --
  -- This can serve as a supervised learning target
  -- for a volunteer propensity model.
  --
  -- IMPORTANT:
  -- If did_volunteer is the prediction target,
  -- shift_count must not be included as a model input
  -- because it directly determines the target value.


FROM `project.dataset.ml_features_table_v2`
-- Reads the table created in the previous step
-- and appends the did_volunteer column.


;
-- Ends the query and writes the final ml_features_table_v2.
--
-- The resulting table contains the original voter features,
-- the most recent qualifying volunteer response classification,
-- and the recorded volunteer participation indicator.
