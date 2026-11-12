
-- ============================================================
-- VOLUNTEER PROPENSITY MODEL:
-- CONTACTABILITY, HOUSEHOLD, AND DEMOGRAPHIC FEATURE ENGINEERING
-- ============================================================
--
-- This script creates 'ml_features_table_v3' by adding contactability,
-- household, recency, and demographic features to the existing
-- 'ml_features_table_v2'.
--
-- These features are used as predictors in a machine learning model
-- designed to estimate a voter's propensity to volunteer.
--
-- The additional features capture several aspects of a voter:
--
-- 1. Contact Information Availability:
--    Whether an email address or address record exists.
--
-- 2. Contactability:
--    Whether the voter has a do-not-contact restriction
--    associated with specific outreach methods.
--
-- 3. Contact Channel Availability:
--    The number of outreach channels without recorded restrictions.
--
-- 4. Record Recency:
--    Number of days since the voter's record was last modified.
--
-- 5. Household Information:
--    Number of records associated with the same address ID.
--
-- 6. Demographic Information:
--    Age group derived from the voter's age.
--
-- These features may help the model identify relationships
-- between volunteer participation, voter engagement,
-- contactability, and demographic characteristics.
--
-- The resulting table retains all existing features from v2
-- and appends the additional engineered features.
--
-- ============================================================


CREATE OR REPLACE TABLE `project.dataset.ml_features_table_v3` AS
-- Creates a new BigQuery table containing the expanded feature set.
-- If the table already exists, it will be replaced.


SELECT
  *,
  -- Retains all existing columns from ml_features_table_v2.
  --
  -- This allows the new features to be added without removing
  -- any previously engineered predictors.


  -- ============================================================
  -- CONTACT INFORMATION AVAILABILITY
  -- ============================================================

  email_id IS NOT NULL AS has_email,
  -- FEATURE 1: EMAIL AVAILABILITY
  --
  -- Creates a Boolean feature indicating whether the voter
  -- has an email identifier associated with their record.
  --
  -- TRUE  = email identifier exists.
  -- FALSE = email identifier is missing.
  --
  -- MODEL RELEVANCE:
  -- May provide information about the availability of
  -- digital contact information for potential volunteers.
  --
  -- Note: This does not guarantee that the email address
  -- is valid or deliverable.


  contacts_address_id IS NOT NULL AS has_address_on_file,
  -- FEATURE 2: ADDRESS AVAILABILITY
  --
  -- Creates a Boolean feature indicating whether an address
  -- identifier exists in the contact information.
  --
  -- TRUE  = address identifier exists.
  -- FALSE = address identifier is missing.
  --
  -- MODEL RELEVANCE:
  -- May help distinguish voters with more complete
  -- contact information.
  --
  -- Note: This is a proxy for having an address on file,
  -- not a guarantee that the address is deliverable.


  -- ============================================================
  -- CONTACTABILITY FEATURES
  -- ============================================================
  --
  -- These features indicate whether a voter has a recorded
  -- do-not-contact restriction for a specific outreach channel.
  --
  -- COALESCE replaces NULL values with FALSE before applying NOT.
  --
  -- Therefore, a missing restriction flag is treated as
  -- having no recorded restriction.
  --
  -- These features do not independently verify that usable
  -- contact information exists for each channel.


  NOT COALESCE(is_do_not_call, FALSE)
    AS can_contact_call,
  -- FEATURE 3: PHONE CALL CONTACTABILITY
  --
  -- TRUE  = no recorded do-not-call restriction.
  -- FALSE = voter has a do-not-call restriction.
  --
  -- MODEL RELEVANCE:
  -- Indicates whether calling is restricted according
  -- to the available contact preference flags.


  NOT COALESCE(is_do_not_text, FALSE)
    AS can_contact_text,
  -- FEATURE 4: TEXT MESSAGE CONTACTABILITY
  --
  -- TRUE  = no recorded do-not-text restriction.
  -- FALSE = voter has a do-not-text restriction.
  --
  -- MODEL RELEVANCE:
  -- Captures the availability of texting as an
  -- unrestricted outreach channel.


  NOT COALESCE(is_do_not_walk, FALSE)
    AS can_contact_walk,
  -- FEATURE 5: DOOR-TO-DOOR CONTACTABILITY
  --
  -- TRUE  = no recorded do-not-walk restriction.
  -- FALSE = voter has a do-not-walk restriction.
  --
  -- MODEL RELEVANCE:
  -- Indicates whether the voter has a recorded restriction
  -- against door-to-door outreach.


  NOT COALESCE(is_do_not_mail, FALSE)
    AS can_contact_mail,
  -- FEATURE 6: MAIL CONTACTABILITY
  --
  -- TRUE  = no recorded do-not-mail restriction.
  -- FALSE = voter has a do-not-mail restriction.
  --
  -- MODEL RELEVANCE:
  -- Captures whether mail outreach is restricted
  -- according to the available preference flags.


  NOT COALESCE(is_do_not_email, FALSE)
    AS can_contact_email,
  -- FEATURE 7: EMAIL CONTACTABILITY
  --
  -- TRUE  = no recorded do-not-email restriction.
  -- FALSE = voter has a do-not-email restriction.
  --
  -- MODEL RELEVANCE:
  -- Indicates whether email outreach is restricted.
  --
  -- This feature is separate from has_email.
  -- A voter can have no email restriction while also
  -- having no email address on file.


  NOT COALESCE(is_do_not_contact, FALSE)
    AS can_contact_any,
  -- FEATURE 8: GENERAL CONTACT RESTRICTION
  --
  -- TRUE  = no recorded general do-not-contact restriction.
  -- FALSE = voter has a general do-not-contact restriction.
  --
  -- MODEL RELEVANCE:
  -- Provides an indicator of the voter's general
  -- contact restriction status.
  --
  -- This does not mean at least one specific contact
  -- channel is available or permitted.


  -- ============================================================
  -- CONTACT CHANNEL COUNT
  -- ============================================================

  (
    CAST(
      NOT COALESCE(is_do_not_call, FALSE) AS INT64
    )
    +
    CAST(
      NOT COALESCE(is_do_not_text, FALSE) AS INT64
    )
    +
    CAST(
      NOT COALESCE(is_do_not_walk, FALSE) AS INT64
    )
    +
    CAST(
      NOT COALESCE(is_do_not_mail, FALSE) AS INT64
    )
    +
    CAST(
      NOT COALESCE(is_do_not_email, FALSE) AS INT64
    )
  ) AS open_channel_count,
  -- FEATURE 9: OPEN CONTACT CHANNEL COUNT
  --
  -- Counts the number of outreach channels without
  -- recorded do-not-contact restrictions.
  --
  -- Each Boolean condition is converted to an integer:
  --
  -- TRUE  = 1
  -- FALSE = 0
  --
  -- The five channel indicators are then added together.
  --
  -- Possible values: 0 through 5.
  --
  -- Example:
  -- Call  = TRUE
  -- Text  = TRUE
  -- Walk  = FALSE
  -- Mail  = TRUE
  -- Email = FALSE
  --
  -- open_channel_count = 3.
  --
  -- MODEL RELEVANCE:
  -- Provides a rough measure of the number of outreach
  -- channels without recorded restrictions.
  --
  -- Note: This does not measure actual reachability.
  -- A voter may have no restriction for a channel
  -- without having usable contact information.
  --
  -- The general is_do_not_contact flag is not included
  -- in this calculation.


  -- ============================================================
  -- RECORD RECENCY
  -- ============================================================

  DATE_DIFF(
    CURRENT_DATE(),
    DATE(datetime_modified),
    DAY
  ) AS days_since_last_modified,
  -- FEATURE 10: DAYS SINCE LAST RECORD MODIFICATION
  --
  -- Calculates the number of days between the current date
  -- and the date the voter record was last modified.
  --
  -- CURRENT_DATE() returns the current date.
  --
  -- DATE(datetime_modified) converts the modification
  -- timestamp to a date.
  --
  -- DATE_DIFF calculates the difference in days.
  --
  -- Example:
  -- Current date: 2026-10-15
  -- Last modified: 2026-10-05
  --
  -- days_since_last_modified = 10.
  --
  -- MODEL RELEVANCE:
  -- May provide a proxy for how recently a voter's
  -- record has been updated.
  --
  -- IMPORTANT:
  -- This is not equivalent to days since last contact.
  -- Records may be modified for administrative reasons,
  -- automated updates, or other unrelated activities.
  --
  -- Because CURRENT_DATE() is dynamic, the feature value
  -- depends on when the query is executed.


  -- ============================================================
  -- HOUSEHOLD FEATURES
  -- ============================================================

  CASE
    WHEN contacts_address_id IS NOT NULL
    THEN COUNT(*) OVER (
      PARTITION BY contacts_address_id
    )
  END AS household_size,
  -- FEATURE 11: HOUSEHOLD SIZE
  --
  -- Counts the number of records sharing the same
  -- contacts_address_id within ml_features_table_v2.
  --
  -- PARTITION BY groups records by address identifier.
  --
  -- COUNT(*) counts the records associated with each address.
  --
  -- Example:
  -- Three voter records share contacts_address_id = 12345.
  --
  -- household_size = 3 for each of those records.
  --
  -- MODEL RELEVANCE:
  -- Provides a measure of the number of voter records
  -- associated with the same address.
  --
  -- IMPORTANT:
  -- The CASE statement prevents records with missing
  -- address IDs from being grouped into one enormous
  -- artificial household.
  --
  -- Voters without an address ID receive NULL.
  --
  -- This measures records per address, not necessarily
  -- the actual number of people living in the household.
  --
  -- It also assumes the input table does not contain
  -- duplicate voter records that inflate the count.


  -- ============================================================
  -- DEMOGRAPHIC FEATURE ENGINEERING
  -- ============================================================

  CASE
    WHEN age_vf IS NULL THEN NULL
    -- Returns NULL when the voter's age is unavailable.

    WHEN age_vf < 25 THEN '18-24'
    -- Assigns voters younger than 25 to the first age group.

    WHEN age_vf < 35 THEN '25-34'
    -- Assigns voters aged 25 through 34.

    WHEN age_vf < 45 THEN '35-44'
    -- Assigns voters aged 35 through 44.

    WHEN age_vf < 55 THEN '45-54'
    -- Assigns voters aged 45 through 54.

    WHEN age_vf < 65 THEN '55-64'
    -- Assigns voters aged 55 through 64.

    WHEN age_vf < 75 THEN '65-74'
    -- Assigns voters aged 65 through 74.

    ELSE '75+'
    -- Assigns voters aged 75 or older.

  END AS age_bin
  -- FEATURE 12: AGE GROUP
  --
  -- Converts the numerical age_vf field into
  -- categorical age ranges.
  --
  -- Uses age_vf rather than age_calculated to avoid
  -- introducing a redundant age measurement.
  --
  -- MODEL RELEVANCE:
  -- Provides a categorical representation of age that
  -- can be evaluated alongside other model predictors.
  --
  -- Note: The first category is labeled '18-24',
  -- but the SQL also assigns non-null ages below 18
  -- to that category if they exist in the source data.


FROM `project.dataset.ml_features_table_v2`
-- Uses the previously created feature table as the
-- source of all existing voter and model features.


;
-- Ends the query and creates ml_features_table_v3.
--
-- The resulting table contains all features from v2
-- plus twelve additional engineered features.
--
-- These features are intended for use in training,
-- validation, and scoring for the Volunteer Propensity Model.
