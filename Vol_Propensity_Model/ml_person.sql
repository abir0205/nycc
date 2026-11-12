
-- ============================================================
-- VOLUNTEER PROPENSITY MODEL:
-- PERSON-LEVEL DATA PREPARATION AND AGE FEATURE ENGINEERING
-- ============================================================
--
-- This script creates 'ml_person', a cleaned person-level
-- dataset used in the Volunteer Propensity Model pipeline.
--
-- The purpose of this query is to prepare voter information
-- for subsequent machine learning feature engineering.
--
-- The script performs two primary operations:
--
-- 1. COLUMN SELECTION AND REMOVAL
--
--    Removes selected administrative, identifying, and
--    redundant columns from the source voter dataset.
--
--    These include:
--      - Name and suffix fields
--      - Administrative identifiers
--      - Record creation metadata
--      - Employment and occupation information
--      - Selected national voter identifiers
--      - Other excluded source columns
--
--    All columns not explicitly excluded are retained.
--
-- 2. AGE FEATURE ENGINEERING
--
--    Calculates each voter's age in completed years
--    using their date of birth and the current date.
--
--    The calculation accounts for whether the voter's
--    birthday has occurred during the current year.
--
-- The resulting table is subsequently joined with
-- 'ml_survey_questions' to construct 'ml_features_table',
-- the initial combined dataset used in the Volunteer
-- Propensity Model feature engineering pipeline.
--
-- ============================================================


CREATE OR REPLACE TABLE `project.dataset.ml_person` AS
-- Creates the cleaned person-level voter table.
--
-- If the table already exists, it will be replaced.
--
-- The resulting table contains the retained voter
-- attributes and the newly calculated age feature.


SELECT

  * EXCEPT (
    state_code,
    first_name,
    last_name,
    suffix,
    is_bad_address,
    phone_id,
    datetime_created,
    person_committee_id,
    created_by_user_id,
    employer_name,
    occupation_name,
    national_myc_van_id,
    national_myv_van_id,
    national_myc_contacts_address_id
  ),
  -- ============================================================
  -- COLUMN REMOVAL AND FEATURE SELECTION
  -- ============================================================
  --
  -- SELECT * EXCEPT retains all columns from the source
  -- table except those explicitly listed.
  --
  -- This provides a convenient way to remove selected
  -- fields without manually specifying every retained column.
  --
  -- The excluded columns include:
  --
  -- state_code:
  --   State identifier from the source voter record.
  --
  -- first_name, last_name, suffix:
  --   Personal name fields that are not retained in
  --   this version of the modeling dataset.
  --
  -- is_bad_address:
  --   Indicator associated with address quality.
  --
  -- phone_id:
  --   Phone identifier associated with the voter.
  --
  -- datetime_created:
  --   Timestamp indicating when the source record
  --   was created.
  --
  -- person_committee_id:
  --   Administrative identifier associated with
  --   the person record.
  --
  -- created_by_user_id:
  --   Identifier of the user who created the record.
  --
  -- employer_name, occupation_name:
  --   Employment and occupation fields.
  --
  -- national_myc_van_id, national_myv_van_id:
  --   National-level voter identifiers that are
  --   excluded from this modeling table.
  --
  -- national_myc_contacts_address_id:
  --   National-level contact address identifier.
  --
  -- MODEL RELEVANCE:
  -- Removing selected fields helps simplify the dataset
  -- and reduce the number of columns carried into
  -- subsequent feature engineering steps.
  --
  -- IMPORTANT:
  -- The query does not automatically remove all
  -- identifiers, redundant columns, or fields that
  -- may be unsuitable for model training.
  --
  -- Any columns not explicitly listed in EXCEPT
  -- remain in the output.


  -- ============================================================
  -- AGE FEATURE ENGINEERING
  -- ============================================================

  DATE_DIFF(
    CURRENT_DATE(),
    date_of_birth,
    YEAR
  )
  -- Calculates the difference between the current
  -- calendar year and the voter's birth year.
  --
  -- DATE_DIFF with YEAR counts year boundaries.
  --
  -- On its own, this calculation does not determine
  -- whether the voter's birthday has already occurred
  -- during the current year.
  --
  -- Therefore, an additional adjustment is required.


  - IF(
      EXTRACT(MONTH FROM CURRENT_DATE()) * 100
        + EXTRACT(DAY FROM CURRENT_DATE())

      <

      EXTRACT(MONTH FROM date_of_birth) * 100
        + EXTRACT(DAY FROM date_of_birth),

      1,
      0
    ) AS age_calculated
  -- FEATURE: CALCULATED AGE
  --
  -- Calculates the voter's age in completed years
  -- using their date of birth.
  --
  -- The calculation consists of two steps:
  --
  -- STEP 1: CALCULATE THE YEAR DIFFERENCE
  --
  -- DATE_DIFF(CURRENT_DATE(), date_of_birth, YEAR)
  --
  -- Determines the difference between the current
  -- year and the voter's birth year.
  --
  -- STEP 2: ADJUST FOR THE BIRTHDAY
  --
  -- The IF statement compares the current month
  -- and day with the voter's birth month and day.
  --
  -- Multiplying the month by 100 and adding the day
  -- creates a numeric month-day representation.
  --
  -- Example:
  --
  -- November 12 becomes:
  -- 11 * 100 + 12 = 1112
  --
  -- December 20 becomes:
  -- 12 * 100 + 20 = 1220
  --
  -- If the current month-day value is smaller than
  -- the birth month-day value, the voter's birthday
  -- has not yet occurred this year.
  --
  -- In that case, IF returns 1 and the calculation
  -- subtracts one year from the initial difference.
  --
  -- Otherwise, IF returns 0 and no adjustment is made.
  --
  -- EXAMPLE:
  --
  -- Current date: 2026-11-12
  -- Date of birth: 1990-12-20
  --
  -- Initial year difference:
  -- 2026 - 1990 = 36
  --
  -- Birthday comparison:
  -- 1112 < 1220 = TRUE
  --
  -- Birthday has not yet occurred.
  --
  -- age_calculated = 36 - 1 = 35
  --
  -- MODEL RELEVANCE:
  -- Provides a numerical demographic feature
  -- representing the voter's age.
  --
  -- This feature can be evaluated alongside other
  -- demographic, engagement, and participation
  -- variables during model development.
  --
  -- IMPORTANT:
  -- If date_of_birth is NULL, age_calculated
  -- will also be NULL.
  --
  -- Because CURRENT_DATE() is dynamic, the calculated
  -- age depends on when the query is executed.


FROM `project.dataset.tmp_person_myc`;
-- Uses the previously prepared person-level voter dataset
-- as the source for the cleaned modeling table.
--
-- All source rows are retained.
--
-- This query does not filter records or deduplicate
-- voters by myc_van_id.
--
-- The resulting table contains the retained person-level
-- voter attributes and the new age_calculated feature.
--
-- This table is subsequently joined with
-- ml_survey_questions to create the initial
-- machine learning feature table.
