
-- ============================================================
-- VOLUNTEER PROPENSITY MODEL:
-- INITIAL MACHINE LEARNING FEATURE TABLE CREATION
-- ============================================================
--
-- This script creates the initial 'ml_features_table'
-- used in the Volunteer Propensity Model pipeline.
--
-- The purpose of this query is to combine two datasets:
--
-- 1. ml_person:
--    Contains person-level voter information and any
--    previously assembled voter features.
--
-- 2. ml_survey_questions:
--    Contains survey question and response information
--    associated with voters.
--
-- The datasets are combined using myc_van_id,
-- the voter identifier shared between both tables.
--
-- A FULL OUTER JOIN is used to preserve records
-- from both datasets, including voters who appear
-- in only one of the two source tables.
--
-- The resulting table serves as the foundation for
-- subsequent feature engineering, including volunteer
-- response classification, contactability features,
-- household features, and historical voting behavior.
--
-- ============================================================


CREATE OR REPLACE TABLE `project.dataset.ml_features_table` AS
-- Creates the initial machine learning feature table.
--
-- CREATE OR REPLACE TABLE instructs BigQuery to:
--
-- 1. Create the table if it does not already exist.
-- 2. Replace the existing table if it already exists.
--
-- The output contains the combined information
-- from the two source datasets.


SELECT *
-- Selects all columns from both source tables.
--
-- Because the join uses USING (myc_van_id),
-- BigQuery returns a single merged myc_van_id column
-- rather than separate copies from each table.
--
-- All other columns from both datasets are retained.


FROM `project.dataset.ml_person` p
-- Uses ml_person as the first source table.
--
-- Alias 'p' represents the person-level dataset.
--
-- This table provides the voter information and
-- previously assembled features used as the foundation
-- for the machine learning dataset.


FULL OUTER JOIN `project.dataset.ml_survey_questions` s
-- Joins the person-level data with survey response data.
--
-- Alias 's' represents the survey question dataset.
--
-- FULL OUTER JOIN preserves:
--
-- 1. Records with matching myc_van_id values
--    in both source tables.
--
-- 2. Records appearing only in ml_person.
--
-- 3. Records appearing only in ml_survey_questions.
--
-- When a record exists in only one table,
-- columns belonging exclusively to the other table
-- will contain NULL values.
--
-- This ensures that records are not automatically
-- excluded simply because they lack a matching
-- person or survey record.


USING (myc_van_id);
-- Matches records using the shared voter identifier.
--
-- USING is equivalent to joining on:
--
-- p.myc_van_id = s.myc_van_id
--
-- It also merges the join-key columns into a single
-- myc_van_id column in the output.
--
-- IMPORTANT:
-- If a voter has multiple survey response records,
-- that voter may appear multiple times in the output.
--
-- This query does not deduplicate records or enforce
-- one row per voter.
--
-- The resulting table is used as the initial dataset
-- for subsequent Volunteer Propensity Model
-- feature engineering steps.
