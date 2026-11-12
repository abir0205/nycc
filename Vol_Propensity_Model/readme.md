# DVC Volunteer Propensity Model

An XGBoost classifier that ranks people in a VAN-based voter file by how closely they resemble past **Direct Voter Contact (DVC)** volunteers: people who have worked a phone-bank or canvass shift. The ranking helps organizers decide whom to ask first. It does not predict whether a given person will say yes when asked.

The project has two parts:

1. **BigQuery feature tables.** A chain of SQL tables joins VAN person records, voter-file data, event attendance, survey responses, contact information and vote history into one modeling table, `ml_features_table_v5`.
2. **`vol_propensity.py`.** Reads that table, checks it, trains and evaluates the model with cross-validation, selects features, calibrates the scores, writes artifacts to `./output/`, and can upload scores back to BigQuery.

Every table name in this README and in the script uses `project.dataset` as a placeholder. Set your real names in the script's config block (see [Configuration](#configuration)).

**Latest results** (details in [Results](#results)):

- PR-AUC of 0.458, against a 6.6% volunteer rate. 83.5% of the top 0.5% of the list have volunteered, 12.6× the average.
- The top score decile holds 56% of all volunteers.
- Most of the lift comes from people's answers to volunteer-ask surveys. Without those answers, lift at the top falls to 5.2×.

---

## Repository layout

```
.
├── vol_propensity.py   # training, evaluation, scoring, upload
├── README.md
├── .gitignore
├── docs/
│   └── shap_summary.png  # SHAP plot from the latest run
├── resources/          # service-account key (gitignored, never commit)
└── output/             # model artifacts and scores (gitignored)
```

The SQL that builds the feature tables is not included in this repository. The [table descriptions](#what-each-table-does) below document what each step does.

## Quick start

1. **Install dependencies.** `shap` and `matplotlib` are only needed for the SHAP plot.

   ```bash
   pip install xgboost scikit-learn pandas numpy joblib "google-cloud-bigquery[pandas]" google-auth shap matplotlib
   ```

2. **Add credentials.** Put a service-account key at `resources/google_creds.json`. Queries are billed to the key's project. The account needs:
   - BigQuery Job User on that project
   - BigQuery Data Viewer on the source dataset
   - BigQuery Data Editor on the output dataset, if `UPLOAD_SCORES = True`

3. **Point the script at your tables.** Set `SOURCE_TABLE`, `SCORES_TABLE_LATEST` and `SCORES_TABLE_HISTORY` near the top of `vol_propensity.py`.

4. **Run it.**

   ```bash
   python vol_propensity.py
   ```

   The script creates `./output/` if needed and prints its checks, metrics and diagnostics as it goes.

---

## How the SQL tables fit together

### Lineage

All tables live in `project.dataset`.

```
 person_records_myc    voter_file_person    event_attendees
          \                    |                   /
           +-------------------+------------------+
                               |
                               v
                        tmp_person_myc             dvc label, shift counts,
                               |                   matched_analytics
                               v
                           ml_person               survey responses + lookups
                               |                             |
                               |                             v
                               |                    ml_survey_questions
                               |                             |
                               +------ FULL OUTER JOIN ------+
                                              |
                                              v
                                      ml_features_table
                                              |   (patch step)
                                              v
                                  ml_features_table_patched
                                              |
                                              v
                                    ml_features_table_v2     vol_response_tier, vol_yes, did_volunteer
                                              |
                                              v
                                    ml_features_table_v3     contact flags, household_size, recency, age_bin
                                              |
                                              v
                                    ml_features_table_v4     vote history
                                              |   (v5 build)
                                              v
                                    ml_features_table_v5     read by vol_propensity.py
                                              |
                          +-------------------+-------------------+
                          v                                       v
                      ./output/                         dvc_scores_latest
                                                        dvc_scores_history
```

### What each table does

| Table | Grain | Role |
|---|---|---|
| `tmp_person_myc` | One row per VAN MyCampaign person record | Joins person records to the voter file and event attendance. Creates the `dvc` label and `matched_analytics`. |
| `ml_person` | Same as above (not enforced) | Drops identifying and unused columns, adds `age_calculated`. |
| `ml_survey_questions` | One row per survey response | Attaches question and response names to each survey response. |
| `ml_features_table` | One row per person per survey response | Joins people to their survey responses. |
| `ml_features_table_patched` | — | Input to v2. Its build step isn't documented here. |
| `ml_features_table_v2` | Intended one row per person | Adds each person's latest answer to a volunteer ask. |
| `ml_features_table_v3` | Same | Adds contact, household and recency fields. |
| `ml_features_table_v4` | Same | Adds vote history. |
| `ml_features_table_v5` | Same | The modeling table the script reads. Its build step isn't documented here. |

#### `tmp_person_myc`: people, the label and the match flag

- Starts from `person_records_myc`, the VAN MyCampaign person records.
- Left-joins `voter_file_person`, deduplicated to one row per `myv_van_id`, for demographics, geography, party, registration and earliest-vote fields. `matched_analytics` is TRUE when a voter-file row matched.
- Left-joins `event_attendees`, grouped to one row per `myc_van_id`:
  - `dvc` is 1 if any of the person's attendance records is a **Phonebanker** or **Canvasser** shift, and 0 otherwise, including people with no attendance at all.
  - `shift_count` counts all attendance records. `dvc_shift_count` counts Phonebanker and Canvasser records only.
  - `volunteer_activity_name` lists the person's distinct activities, comma-separated.
- There is no date filter, so `dvc` counts any DVC shift ever recorded.

#### `ml_person`: person-level cleanup

Drops 13 columns from `tmp_person_myc` (name fields, administrative IDs, employment fields and a few others) and adds `age_calculated` from date of birth. Because it subtracts a year when the birthday hasn't happened yet, the value depends on the date the SQL runs.

#### `ml_survey_questions`: survey responses

Builds a lookup from the survey response and survey question tables on `survey_question_id`, then joins it to survey response events on `survey_response_id`. It keeps `myc_van_id`, `date_canvassed`, question IDs, names and text, and response IDs and names. Rows without a `myc_van_id` are dropped. A person can have many rows.

#### `ml_features_table`: people joined to survey responses

`FULL OUTER JOIN` of `ml_person` and `ml_survey_questions` `USING (myc_van_id)`. It keeps people with no survey responses and responses with no matching person. A person with several responses gets several rows.

#### `ml_features_table_v2`: volunteer-ask responses

Reads `ml_features_table_patched` and picks each person's latest response to any of three volunteer-ask survey questions (2024 and 2026 cycles), ordered by `date_canvassed` descending, then `datetime_modified`, then `survey_response_id`.

| Column | Definition |
|---|---|
| `vol_response_tier` | 4 = Yes, 3 = Maybe, 2 = a response starting with "Later", 1 = No/Never, 0 = other, missing or never asked |
| `vol_yes` | TRUE when `vol_response_tier = 4`. Stated interest, not a completed shift. |
| `did_volunteer` | TRUE when `COALESCE(shift_count, 0) > 0`. Any recorded shift, not only DVC. |

Response-level columns are then removed and `SELECT DISTINCT *` collapses identical rows. Rows that still differ in any column survive, so this does not guarantee one row per person.

#### `ml_features_table_v3`: contact, household and age

Adds 12 columns:

| Family | Columns |
|---|---|
| Contact information | `has_email`, `has_address_on_file` |
| Contact restrictions | `can_contact_call`, `can_contact_text`, `can_contact_walk`, `can_contact_mail`, `can_contact_email`, `can_contact_any` |
| Available channels | `open_channel_count` (0–5 channels with no recorded restriction) |
| Recency | `days_since_last_modified` (last record update, not last contact) |
| Household | `household_size` (records sharing a non-null `contacts_address_id`) |
| Age | `age_bin`, from `age_vf` |

`can_contact_*` means no restriction was recorded. It does not confirm a working phone, email or address. Null do-not-contact flags are treated as false. `household_size` counts records in the table, not verified household members.

#### `ml_features_table_v4`: vote history

Unpivots general-election fields for 2014–2025 and Democratic primary fields for 2020–2024, aggregates them to one row per `person_id`, and joins them back to v3.

| Column | Definition |
|---|---|
| `general_votes_last3` | General elections voted in 2020, 2022 and 2024 |
| `offyear_votes_last3` | Elections voted in 2021, 2023 and 2025 |
| `voted_all_recent_generals` | Voted in all three of 2020, 2022 and 2024 |
| `longest_gap_years` | Longest gap between consecutive recorded general-election votes, 2014–2025 |
| `primary_dem_votes_last5` | Democratic primaries voted, 2020–2024 |
| `voted_early_last_general` | Voted early in the 2025 general |
| `voted_absentee_last_general` | Voted absentee in the 2025 general |

The aggregates are one row per person. The two vote-method flags come from a final join to the raw vote table, which can multiply rows if that table has more than one matching record for a person. BigQuery's `UNPIVOT` skips nulls by default, so a missing vote record and a non-vote can look the same.

#### `ml_features_table_v5`: the modeling table

This is the only table `vol_propensity.py` reads. Its build step isn't documented here. `myv_van_id` is a STRING in v5. In the latest run, v5 contained all 22 candidate features, including the v4 vote-history columns. If a rebuilt v5 drops a column, the script's startup check prints it.

---

## How the script uses the tables

### Reading `ml_features_table_v5`

The script touches v5 in three stages.

**1. Schema check (metadata only, no query).** It reads the table's schema and:
- stops if `myv_van_id`, `dvc` or `is_deceased` is missing
- stops if any column in `FORBIDDEN` was added to `FEATURES`
- prints any candidate feature the table doesn't have and leaves it out

**2. Preflight queries.** Three small queries, printed for you to read:

| Check | What it shows |
|---|---|
| Label family | For each `dvc` value, how many rows have `dvc_shift_count > 0`, `shift_count > 0` and `did_volunteer`. Every `dvc = 1` row should have `dvc_shift_count > 0`. |
| Match status | DVC rate for rows with and without a `myv_van_id`. Warns if the rates differ by more than one point, because missing voter-file fields can then stand in for the label. |
| Duplicates | Rows versus distinct `myv_van_id` values. |

`dvc_shift_count`, `shift_count` and `did_volunteer` are read only here, never as features, but the preflight query fails if they're missing.

**3. The modeling pull.** One query loads everything the model uses:

```sql
SELECT
  NULLIF(myv_van_id, '') AS myv_van_id,
  CAST(dvc AS INT64) AS label,
  <candidate features present in the table>
FROM `project.dataset.ml_features_table_v5`
WHERE NOT COALESCE(is_deceased, FALSE)   -- unknown deceased status is kept
  AND dvc IS NOT NULL
```

In Python, rows with a blank `myv_van_id` count as unmatched and are dropped by default, since they can't be handed to an organizer. Duplicate IDs are collapsed to one row, keeping a positive row if there is one.

### Where each feature comes from

| Family | Columns | Built in |
|---|---|---|
| Person and voter file | `age_vf`, `sex`, `ethnicity_modeled`, `county_name`, `us_cong_district_latest`, `state_senate_district_latest`, `state_house_district_latest`, `party_id`, `voter_type_id`, `person_years_since_earliest_reg`, `years_since_earliest_vote`, `earliest_activity_type`, `matched_analytics` | `tmp_person_myc` |
| Stated interest | `vol_yes`, `vol_response_tier` | v2 |
| Contact and household | `has_email`, `household_size` | v3 |
| Vote history | `general_votes_last3`, `offyear_votes_last3`, `primary_dem_votes_last5`, `voted_early_last_general`, `longest_gap_years` | v4 |

### Built upstream but not used as features

| Columns | Built in | Why not a feature |
|---|---|---|
| `dvc`, `shift_count`, `dvc_shift_count`, `volunteer_activity_name` | `tmp_person_myc` | The label or a restatement of it |
| `did_volunteer` | v2 | Restates the label |
| `days_since_last_modified`, `datetime_modified`, `years_since_earliest_activity` | v3 / upstream | Updated when people act, so they leak the outcome |
| `can_contact_*`, `open_channel_count`, `has_address_on_file` | v3 | Whether you *can* reach someone, not whether they'd volunteer, and some are set after contact. Use them to filter call lists. |
| `age_bin`, `age_calculated` | v3, `ml_person` | Duplicate `age_vf` |
| `voted_all_recent_generals`, `voted_absentee_last_general` | v4 | Not in the candidate list |
| `myc_van_id`, `myv_van_id`, `person_id`, `email_id`, `contacts_address_id` | various | Identifiers |

### Writing the score tables

With `UPLOAD_SCORES = True`, scores go straight from the DataFrame to BigQuery:

| Table | Write mode | Use |
|---|---|---|
| `dvc_scores_latest` | Replaced every run | The current ranking. What organizers and reports should query. |
| `dvc_scores_history` | Appended every run, partitioned by day on `scored_at` | Snapshots, so past rankings can be checked against who later volunteered. |

Both tables have the same columns:

| Column | Type | Meaning |
|---|---|---|
| `myv_van_id` | STRING | Person ID |
| `score` | FLOAT64 | Raw model output, for ranking |
| `est_chance` | FLOAT64 | Calibrated estimate (see [Reading the scores](#reading-the-scores)) |
| `already_volunteered` | BOOL | `dvc = 1` in the training data |
| `decile` | INT64 | 1–10 among prospects, 1 = best. Null for existing volunteers. |
| `scored_at` | TIMESTAMP | Run time (UTC) |
| `feature_set` | STRING | `selected` or `full`, whichever model shipped |
| `roc_auc_cv` | FLOAT64 | Cross-validated ROC-AUC of the shipped model |
| `source_table` | STRING | The table the run read |

Safeguards:
- The upload is skipped if the shipped model's cross-validated ROC-AUC is above 0.90, which on this kind of data usually means a leaking feature.
- It refuses to upload an empty score set or one with duplicate IDs.
- The history table accepts new columns, and older rows show NULL for them. It will reject the append if an existing history table stores `myv_van_id` as INT64. Use a new table name in that case.

### Example queries

Top-decile prospects with no recorded call restriction:

```sql
SELECT s.myv_van_id, s.score, s.est_chance
FROM `project.dataset.dvc_scores_latest` AS s
WHERE s.decile = 1
  AND s.myv_van_id IN (
    SELECT myv_van_id
    FROM `project.dataset.ml_features_table_v5`
    WHERE can_contact_call
  )
ORDER BY s.score DESC;
```

This uses `IN` rather than a join because v5 can hold more than one row per `myv_van_id`.

Whether people ranked on a past run went on to volunteer. Run this after the feature tables have been rebuilt with newer attendance data:

```sql
SELECT
  h.decile,
  COUNT(*)   AS prospects,
  AVG(f.dvc) AS later_dvc_rate
FROM `project.dataset.dvc_scores_history` AS h
JOIN (
  SELECT myv_van_id, MAX(dvc) AS dvc
  FROM `project.dataset.ml_features_table_v5`
  GROUP BY myv_van_id
) AS f USING (myv_van_id)
WHERE DATE(h.scored_at) = DATE '2026-10-01'  -- the run to evaluate
  AND NOT h.already_volunteered
GROUP BY h.decile
ORDER BY h.decile;
```

If the model works, `later_dvc_rate` should fall steadily from decile 1 to decile 10.

---

## Training workflow

1. **Schema check and preflight** (above).
2. **Load and clean.** Drop unmatched rows (`DROP_UNMATCHED`) and collapse duplicate IDs.
3. **Audit.** Print the positive rate, columns more than 5% null (flagging groups of columns with the same null rate, which usually means one failed upstream join), near-constant columns and category counts.
4. **Prepare.** Columns in `CATEGORICAL` become pandas categories, so integer codes like `voter_type_id` aren't treated as ordered numbers. Everything else becomes float. The run stops if a numeric column comes out entirely blank, which means it was text or a date that needed handling.
5. **Cross-validate with all features.** Five stratified folds. Each fold's model stops early on a 15% slice of its own training rows, never on the rows it's graded on. Positives are weighted by the negative-to-positive ratio.
6. **Ablations.** Refit without the `stated_interest` columns (`vol_yes`, `vol_response_tier`) and, separately, without the `contact_records` columns (`has_email`, `matched_analytics`). The script warns if more than half the lift at the top 0.5% disappears.
7. **Feature selection inside each fold.** Every feature, or group of features (`vote_history`, `districts`, `stated_interest`), gets a shuffled "shadow" copy. A unit is kept if its mean |SHAP| beats 1.5× its own shadow in a majority of three fits. Selection sees only that fold's training rows, using all positives and up to 200,000 negatives.
8. **Choose what ships.** The final feature set is every unit kept in at least four of five folds. The selected model ships only if its PR-AUC and precision at the top 0.5% are within one fold standard deviation of the full model. Otherwise the full model ships.
9. **Calibrate.** Isotonic regression maps out-of-fold scores to `est_chance`. A cross-fitted version, where each fold is calibrated on the others, feeds the decile check.
10. **Final fit and outputs.** Refit on all rows at 1.2× the median fold tree count, compute SHAP importance, write the files and upload.

Every reported metric, the decile table, the county table and the exported scores are out-of-fold: each person is scored by a model that never saw them.

### Metrics

| Metric | Meaning |
|---|---|
| ROC-AUC | How well volunteers rank above non-volunteers overall |
| PR-AUC | Precision–recall summary, more informative than ROC-AUC for a rare label |
| Precision@top0.5% | Share of actual DVC volunteers in the top 0.5% of scores |
| Lift@top0.5% | That precision divided by the overall volunteer rate |
| Decile table | Actual volunteer rate and mean `est_chance` per score decile |

---

## Results

These results come from the latest run on `ml_features_table_v5`. After dropping unmatched rows and collapsing duplicate IDs, the run modeled **206,499 people, 13,662 (6.6%) of whom have worked a DVC shift**. Every number is out-of-fold, and ± is the standard deviation across the five folds.

### Performance

| Model | ROC-AUC | PR-AUC | Precision @ top 0.5% | Lift @ top 0.5% |
|---|---|---|---|---|
| **Shipped: selected features (19)** | **0.855 ± 0.004** | **0.458 ± 0.013** | **0.835 ± 0.025** | **12.6×** |
| All 22 candidates | 0.855 ± 0.004 | 0.459 ± 0.014 | 0.832 ± 0.030 | 12.6× |
| Without stated interest (`vol_yes`, `vol_response_tier`) | 0.726 ± 0.005 | 0.167 ± 0.007 | 0.341 ± 0.030 | 5.2× |
| Without contact records (`has_email`, `matched_analytics`) | 0.856 ± 0.004 | 0.459 ± 0.014 | 0.843 ± 0.027 | 12.7× |

A random ranking would score a PR-AUC of about 0.066, the volunteer rate. The top 0.5% of the list is 1,032 people, and 862 of them have volunteered.

- **Stated interest carries most of the model.** Without `vol_yes` and `vol_response_tier`, PR-AUC falls by about two-thirds and lift at the top drops from 12.6× to 5.2×, so the script's ablation warning fires. The model still ranks well above chance without them, but its headline numbers depend on when those fields are written (see [Known limitations](#known-limitations)).
- **The contact-record fields add nothing.** Removing `has_email` and `matched_analytics` lowers no metric, and feature selection dropped both in every fold. Their open timing question therefore doesn't affect the shipped model.
- **Feature selection kept 19 of 22 candidates.** `earliest_activity_type`, `has_email` and `matched_analytics` were kept in none of the five folds. `sex` was kept in four, exactly the threshold. The selected model passed the shipping check: PR-AUC 0.458 against a floor of 0.445, and precision 0.835 against a floor of 0.802.

### Compared with the earlier v4 run

The v4 run had the same 206,499 people and 13,662 volunteers. v5 added five candidates: `state_house_district_latest`, `voter_type_id`, `vol_response_tier`, `has_email` and `matched_analytics`.

| Run | Features shipped | ROC-AUC | PR-AUC | Precision @ top 0.5% | Lift @ top 0.5% |
|---|---|---|---|---|---|
| v4 | 16 | 0.811 | 0.407 | 0.819 | 12.4× |
| v5 | 19 | 0.855 | 0.458 | 0.835 | 12.6× |
| v4 without `vol_yes` | — | 0.727 | 0.162 | 0.308 | 4.7× |
| v5 without stated interest | — | 0.726 | 0.167 | 0.341 | 5.2× |

Almost all of v5's gain comes from `vol_response_tier`. With stated interest removed, the two runs are nearly level (PR-AUC 0.162 vs 0.167), so the new district and voter-type features add little. v5 is better mainly because it leans harder on the same timing-unverified signal.

### Decile lift

All 206,499 people, split into ten equal groups by out-of-fold score:

| Decile | People | Volunteers | Volunteer rate | Lift | Cumulative share of all volunteers |
|---|---|---|---|---|---|
| 1 | 20,650 | 7,592 | 36.8% | 5.6× | 56% |
| 2 | 20,650 | 2,106 | 10.2% | 1.5× | 71% |
| 3 | 20,650 | 1,235 | 6.0% | 0.9× | 80% |
| 4 | 20,650 | 844 | 4.1% | 0.6× | 86% |
| 5 | 20,649 | 564 | 2.7% | 0.4× | 90% |
| 6 | 20,650 | 483 | 2.3% | 0.4× | 94% |
| 7 | 20,650 | 351 | 1.7% | 0.3× | 96% |
| 8 | 20,650 | 243 | 1.2% | 0.2× | 98% |
| 9 | 20,650 | 135 | 0.7% | 0.1× | 99% |
| 10 | 20,650 | 109 | 0.5% | 0.1× | 100% |

The top decile holds 56% of all volunteers at 5.6× the average rate, and the top three deciles hold 80%. The rate falls in every decile, so the ranking works all the way down the list, not only at the top.

### Prospect deciles

These are the deciles organizers see: the 192,837 people who haven't volunteered, in groups of about 19,300 by score (the `decile` column).

| Decile | People | Mean `est_chance` | Range |
|---|---|---|---|
| 1 | 19,284 | 23.7% | 11.9% – 89.0% |
| 2 | 19,284 | 8.3% | 6.9% – 11.9% |
| 3 | 19,283 | 5.0% | 4.1% – 6.9% |
| 4 | 19,284 | 3.6% | 2.8% – 4.1% |
| 5 | 19,283 | 2.7% | 2.6% – 2.8% |
| 6 | 19,284 | 2.1% | 1.7% – 2.6% |
| 7 | 19,284 | 1.6% | 1.2% – 1.7% |
| 8 | 19,283 | 1.1% | 0.8% – 1.2% |
| 9 | 19,284 | 0.6% | 0.6% – 0.8% |
| 10 | 19,284 | 0.5% | 0.0% – 0.6% |

For a prospect, `est_chance` is the share of people with a similar score who have worked a DVC shift. It isn't the chance this person says yes when asked. In the top prospect decile it averages 24% and reaches 89%, and 1,724 prospects are at 50% or higher.

### What drives the score

![SHAP summary for the shipped model](docs/shap_summary.png)

Each dot is one person. Its horizontal position is how far that feature moved the person's score, in log-odds, and its color is the feature's value: red for high, blue for low, gray for categories or missing values. Features are ordered by their mean absolute SHAP value:

| Feature | Mean abs. SHAP | Feature | Mean abs. SHAP |
|---|---|---|---|
| `vol_response_tier` | 0.78 | `party_id` | 0.08 |
| `offyear_votes_last3` | 0.33 | `household_size` | 0.07 |
| `state_house_district_latest` | 0.24 | `person_years_since_earliest_reg` | 0.06 |
| `vol_yes` | 0.20 | `sex` | 0.05 |
| `county_name` | 0.17 | `general_votes_last3` | 0.03 |
| `voted_early_last_general` | 0.14 | `longest_gap_years` | 0.03 |
| `age_vf` | 0.12 | `years_since_earliest_vote` | 0.02 |
| `state_senate_district_latest` | 0.12 | `voter_type_id` | 0.02 |
| `us_cong_district_latest` | 0.08 | `primary_dem_votes_last5` | 0.01 |
| `ethnicity_modeled` | 0.08 | | |

What the plot shows:

- **Stated interest dominates.** A high `vol_response_tier` ("Yes") raises the score by up to about 2.5. A low tier (No, or never asked) lowers it by about 0.7. `vol_yes` adds up to about 1.4 more.
- **Off-year voting is the strongest signal outside the survey.** People who vote in off-year elections score higher and those who don't score lower. Voting early in the last general also helps.
- **Age and registration length lower the score.** The oldest people score noticeably lower, as do people registered the longest. Long gaps between votes lower it slightly.
- **Geography moves scores in both directions.** This covers state house, county, state senate and congressional districts. Much of it likely reflects where the program has organized rather than where people are more willing (see [Known limitations](#known-limitations), opportunity bias).
- **`voter_type_id` is minor overall but sharp for a few people.** Some codes lower the score by 2–3.
- **Modeled ethnicity, party and sex have small effects**, but they are in the model. See the sensitive-attributes limitation.

---

## Configuration

All settings are constants at the top of `vol_propensity.py`.

| Setting | Default | Effect |
|---|---|---|
| `SOURCE_TABLE` | `project.dataset.ml_features_table_v5` | Modeling table |
| `SCORES_TABLE_LATEST`, `SCORES_TABLE_HISTORY` | `project.dataset.dvc_scores_*` | Output tables |
| `UPLOAD_SCORES` | `True` | Upload scores to BigQuery |
| `KEEP_HISTORY` | `True` | Append each run to the history table |
| `DROP_UNMATCHED` | `True` | Drop rows with no `myv_van_id`. `False` keeps them with a `vf_matched` flag. |
| `RUN_ABLATION` | `True` | Run the ablation refits |
| `SELECT_FEATURES` | `True` | Run in-fold feature selection |
| `N_FOLDS` | `5` | Cross-validation folds |
| `K_FRAC` | `0.005` | Top-of-list cut for precision and lift |
| `FEATURES` | 22 columns | Candidate features |
| `CATEGORICAL` | — | Which candidates are categories |
| `FORBIDDEN` | — | Columns that may never be features (checked at startup) |
| `FEATURE_GROUPS` | — | Features judged together during selection |
| `ABLATIONS` | — | Column sets removed in the ablation refits |

---

## Outputs

### Local files (`./output/`)

| File | Contents |
|---|---|
| `dvc_model.json` | Final XGBoost model, trained on all rows with the shipped feature set |
| `dvc_calibrator.joblib` | Isotonic mapping from `score` to `est_chance` |
| `dvc_scores.csv` | Out-of-fold `score`, `est_chance`, `already_volunteered` and `decile` per `myv_van_id` |
| `feature_importance.csv` | SHAP importance for the shipped model and how many folds kept each feature |
| `metrics.json` | Full-model, selected-model and ablation metrics, the final feature list and run settings |
| `shap_summary.png` | SHAP beeswarm plot (needs `shap` and `matplotlib`) |

`output/` is gitignored, and none of these artifacts belong in a public repo. `dvc_scores.csv` contains VAN IDs, and `dvc_model.json` stores the category labels it was trained on, such as county names and district numbers. The SHAP plot in `docs/` is copied from a run because it contains no personal data.

### Reading the scores

- **`score`** is for ranking only. Because training up-weights volunteers, it runs high: 0.5 is roughly an average person, not a coin flip.
- **`est_chance`** is the calibrated estimate of how often people with that score have worked a DVC shift. It is not the rate at which they'll say yes when asked. That rate is only known once outreach results are tracked against `dvc_scores_history`.
- **`decile`** ranks prospects (people who haven't volunteered) from 1 (best) to 10. Existing volunteers get a null decile.

The exported scores come from the cross-validation models, not from `dvc_model.json`. The saved model and calibrator are for scoring people added later. The script has no scoring-only mode, so new data must be prepared the same way as in `prepare()` and use the columns listed under `final_features` in `metrics.json`. The final model uses more trees than the fold models, so treat its `est_chance` as approximate until the next full run.

### Using the scores

The model is a ranking aid. A high score means a person looks like past DVC volunteers in this data. It doesn't show they're willing, and past volunteering partly reflects where the program has organized before. The county table the script prints helps here: a county scoring well below its own volunteer rate is usually one the program under-organized, not one where people are less willing.

For a call list, start from the ranking and then apply current contact restrictions, channel availability, recent outreach and existing volunteer status. Those fields are deliberately left out of the model so they can be applied as filters.

---

## Known limitations


1. **Row multiplication.** The person-to-survey join produces one row per response. `SELECT DISTINCT` in v2 doesn't guarantee one row per person, and the v4 vote-method join can add duplicates. The script collapses duplicate IDs, keeping a positive row, but that is a safeguard, not a fix. Duplicates should be resolved upstream.
2. **No time window on the label.** `dvc` counts any DVC shift ever recorded, and features are not cut off at a prediction date. Fields like `vol_yes` may be written during the same recruitment that produced the shift. The ablations measure how much the model leans on such fields. They can't show the fields were available beforehand.
3. **Random folds.** Stratified random cross-validation measures performance on held-out people from the same population. It doesn't measure performance on a future recruitment cycle.
4. **Voter-file coverage.** Rows without a `myv_van_id` are dropped by default, and they may differ systematically from matched rows.
5. **Opportunity bias.** Past volunteering reflects where the program asked people, so geography and some contact features partly encode program reach rather than willingness.
6. **Calibration for new people.** The calibrator was fit on out-of-fold scores from models with fewer trees than the saved final model. Check `est_chance` against real outcomes.
7. **Sensitive attributes.** The candidate features include modeled ethnicity, sex and age. Review fairness, applicable rules and whether each feature suits the intended use before deployment.

## Before a production run

- [ ] v5 was rebuilt from the intended upstream tables.
- [ ] v5's `dvc` still means a recorded Phonebanker or Canvasser shift, as built in `tmp_person_myc`.
- [ ] Every predictor is available as of the date the scores will be used, or is excluded.
- [ ] The startup log's list of missing features is expected.
- [ ] `myv_van_id` coverage and duplicate counts from the preflight look reasonable.
- [ ] Null-rate clusters flagged by the audit are explained.
- [ ] Full-model, selected-model and ablation metrics have been read together.
- [ ] Current contact restrictions are applied outside the model before outreach.
- [ ] Past score snapshots are compared against later outcomes to measure real lift.