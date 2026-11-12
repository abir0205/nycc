
-- ============================================================
-- VOLUNTEER PROPENSITY MODEL:
-- SURVEY QUESTION AND RESPONSE DATA PREPARATION
-- ============================================================
--
-- This script creates 'ml_survey_questions', a table
-- containing voter survey responses and their associated
-- survey question information.
--
-- The purpose of this query is to combine three sources:
--
-- 1. survey_responses:
--    Contains survey response identifiers and their
--    corresponding response names.
--
-- 2. survey_questions:
--    Contains survey question identifiers, names,
--    descriptions, and question types.
--
-- 3. responses_myc:
--    Contains individual voter survey response records,
--    including voter identifiers and canvass dates.
--
-- The resulting table connects each survey response
-- to the voter who provided it and the question
-- associated with that response.
--
-- This table is subsequently joined with 'ml_person'
-- to construct the initial machine learning feature table.
--
-- In later stages of the Volunteer Propensity Model
-- pipeline, selected volunteer survey questions are used
-- to create volunteer response tiers and labels.
--
-- ============================================================


CREATE OR REPLACE TABLE `project.dataset.ml_survey_questions` AS
-- Creates the survey response feature table.
--
-- If the table already exists, it will be replaced.
--
-- The output contains voter identifiers, canvass dates,
-- survey question information, and survey response values.


-- ============================================================
-- SURVEY QUESTION AND RESPONSE LOOKUP
-- ============================================================

WITH sqr AS (
-- Creates a temporary lookup table connecting survey
-- responses to their corresponding survey questions.
--
-- The lookup is constructed using survey_response_id
-- and survey_question_id.
--
-- This allows individual voter response records to be
-- enriched with readable question and response information.

  SELECT

    sq.survey_question_id,
    -- Unique identifier associated with the survey question.
    --
    -- Used to identify the question connected to
    -- each survey response.


    sq.survey_question_name,
    -- Name of the survey question.
    --
    -- Examples used later in the model pipeline include:
    --
    -- 'SQ24 DCCC VOL'
    -- '2024-LG-Vol Ask'
    -- '26 GE Volunteer'
    --
    -- These question names are used downstream to identify
    -- survey responses related to volunteer interest.


    sq.survey_question_text,
    -- Full text or description of the survey question.
    --
    -- Provides additional context about the question
    -- associated with each response.


    sq.survey_question_type,
    -- Indicates the type of survey question.
    --
    -- This field is included in the temporary lookup
    -- but is not selected in the final output table.


    sr.survey_response_id,
    -- Unique identifier associated with a survey response.
    --
    -- Used to connect the response lookup to individual
    -- voter survey response records.


    sr.survey_response_name,
    -- Human-readable name of the survey response.
    --
    -- For volunteer-related questions, downstream
    -- classification logic recognizes responses such as:
    --
    -- Yes
    -- Maybe
    -- Later
    -- No
    -- Never
    --
    -- These values are later converted into numerical
    -- volunteer interest tiers.

  FROM `project.dataset.survey_responses` sr
  -- Uses the survey response lookup as the starting table.
  --
  -- Each record represents a survey response option
  -- associated with a survey question.


  LEFT JOIN `project.dataset.survey_questions` sq
    USING (survey_question_id)
  -- Connects each survey response option to its
  -- corresponding survey question.
  --
  -- USING (survey_question_id) matches the shared
  -- survey question identifier.
  --
  -- LEFT JOIN preserves survey response options even
  -- when a matching survey question record is missing.
  --
  -- In those cases, the survey question fields
  -- will contain NULL values.
)


-- ============================================================
-- VOTER SURVEY RESPONSE RECORDS
-- ============================================================

SELECT

  r.myc_van_id,
  -- Unique voter identifier used in MyCampaigns.
  --
  -- This identifier connects survey responses to
  -- person-level voter records in the model pipeline.
  --
  -- It is also used downstream to consolidate
  -- multiple survey responses belonging to one voter.


  r.date_canvassed,
  -- Date on which the survey response was recorded
  -- during voter contact or canvassing.
  --
  -- This field is important for identifying the most
  -- recent volunteer survey response.
  --
  -- In ml_features_table_v2, responses are ranked
  -- primarily by date_canvassed to select the most
  -- recent qualifying response per voter.


  sqr.survey_question_id,
  -- Adds the identifier of the survey question
  -- associated with the voter's response.


  sqr.survey_question_name,
  -- Adds the survey question name.
  --
  -- This field is subsequently used to filter
  -- for the three designated volunteer questions.


  sqr.survey_question_text,
  -- Adds the full survey question text.
  --
  -- Provides context for the response recorded
  -- during voter contact.


  sqr.survey_response_id,
  -- Adds the identifier of the survey response.
  --
  -- This field is used downstream as a tie-breaker
  -- when identifying the most recent volunteer response.


  sqr.survey_response_name
  -- Adds the human-readable response value.
  --
  -- This is the field used downstream to classify
  -- volunteer interest.
  --
  -- For example:
  --
  -- Yes   -> Tier 4
  -- Maybe -> Tier 3
  -- Later -> Tier 2
  -- No    -> Tier 1
  --
  -- These classifications are created in
  -- ml_features_table_v2, not in this query.


FROM `project.dataset.responses_myc` r
-- Uses the voter-level survey response records
-- as the primary source.
--
-- Each record represents a survey response
-- associated with a voter and canvass event.


JOIN sqr
  USING (survey_response_id)
-- Connects individual voter response records to
-- the survey question and response lookup.
--
-- The join matches records using survey_response_id.
--
-- Because this is an INNER JOIN, only voter responses
-- with matching survey response lookup records
-- are retained.
--
-- Records without matching lookup entries
-- are excluded from the output.


WHERE r.myc_van_id IS NOT NULL;
-- Excludes survey response records without
-- an associated MyCampaigns voter identifier.
--
-- This ensures that every output record has
-- a non-null identifier that can be used to
-- connect it to the person-level feature table.
--
-- IMPORTANT:
-- This does not guarantee one record per voter.
--
-- A voter may have answered multiple survey questions
-- or provided multiple responses over time.
--
-- Therefore, the output may contain multiple records
-- for the same myc_van_id.
--
-- These records are consolidated in later stages
-- of the Volunteer Propensity Model pipeline.
