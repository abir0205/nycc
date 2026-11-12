# Phase 2 Direct Voter Contact Universe

## Overview

The **Phase 2 Direct Voter Contact Universe** is the primary voter targeting dataset used by the New York State Democratic Party for direct voter outreach during Phase 2 of the 2026 campaign.

The pipeline is implemented in Google BigQuery. It combines voter-file data, predictive scores, geographic and district-specific targeting universes, contact history, and survey responses. The final table, `project.dataset.combined_universe`, is the master Phase 2 direct voter contact universe.

Its purposes are to identify likely supporters with lower modeled turnout, identify voters meeting persuasion criteria, consolidate targeting sources, and exclude voters based on recent contacts, negative contact results, opposition responses, and high-support/high-turnout combinations.

## 1. Data Sources and Scores

The `base_universe` joins voter-file records to governor support and persuasion scores, turnout scores, and DNC support scores by `person_id`. The score-source CTEs deduplicate by `person_id` before joining. Governor support, governor persuasion, and turnout scores must be non-null; a matching DNC score record is required, but `d.support` itself may be null.

| Score | Meaning | Use in pipeline |
|---|---|---|
| Governor Support (`support_score_v1`) | Modeled support for the gubernatorial candidate | Geographic targeting and high-support exclusion |
| Governor Persuasion (`persuasion_score_v1`) | Modeled persuadability | Higher-turnout non-NYC targeting |
| Clarity 2026 Turnout (`clarity_2026_turnout_v2_1`) | Modeled turnout likelihood | Targeting ranges and high-turnout exclusion |
| DNC Support (`dnc_support_score`) | Support estimate from the DNC model | Final high-support/high-turnout exclusion |
| DCCC CSM (`dccc_csm_score`) | Modeled congressional candidate support | District-specific targeting |

The base query multiplies turnout and DNC support values by 100. The governor scores and DCCC CSM are compared using the thresholds already present in their source queries. Model scores are estimates, not observed voting intentions or guarantees of behavior.

## 2. Geographic Universes

### NYC, Buffalo, and Rochester — `nyc_universe_v2`

Includes voters in Kings, Bronx, New York, Queens, or Richmond counties, **or** voters whose voting city is Buffalo or Rochester.

| Score | Criterion |
|---|---|
| Governor support | ≥ 80 |
| Clarity turnout | 30–70, inclusive |

This group captures higher-modeled-support voters with lower or moderate modeled turnout. The query excludes matching `4 - Lean Oppose` and `5 - Strong Oppose` survey responses. It also contains a high-support/high-turnout exclusion that is redundant given the maximum turnout score of 70.

### Non-NYC — `non_nyc_universe_v2`

Combines two distinct targeting groups:

| Group | Governor support | Governor persuasion | Clarity turnout |
|---|---|---|---|
| 1: Moderate support/turnout | 40–70, inclusive | No additional threshold | ≥ 55 and < 75 |
| 2: Higher turnout/persuasion | No additional threshold | ≥ 40 | ≥ 80 |

After combining the groups, the query excludes Kings, Bronx, New York, and Queens counties; congressional districts `001`, `004`, `017`, `019`, and `021`; and voting cities Buffalo and Rochester. **Richmond County is not excluded by this query.** It also excludes governor support ≥ 90 combined with turnout ≥ 90 and matching opposition survey responses.

## 3. Congressional District Universes

Separate district tables use DCCC candidate support scores or governor support scores:

| District | Support model | Support range | Clarity turnout range |
|---|---|---|---|
| NY-01 | DCCC CSM | 75–90 | 30–65 |
| NY-04 | DCCC CSM | 75–90 | 30–65 |
| NY-17 | DCCC CSM | 75–90 | 30–65 |
| NY-18 | Governor support | 40–80 | 30–80 |
| NY-19 | DCCC CSM | 75–90 | 30–65 |
| NY-21 | Governor support | 40–80 | 30–80 |

All ranges above are inclusive. The DCCC queries use the district identifier in the DCCC scores table (`s.cd`), whereas NY-18 and NY-21 use the base universe congressional district field (`b.us_cong_district_latest`). Each query selects `b.*`, so the joined DCCC CSM score is used for filtering but is not retained as an output column.

**Important:** In the supplied `combined_universe_v2` SQL, all six of these district-specific union statements are commented out. Their tables do not currently contribute to the combined universe through those statements.

## 4. Building the Combined Universe

**Final output:** `project.dataset.combined_universe`

The currently active `UNION ALL BY NAME` inputs are:

1. `Xtmp_dccc`
2. `nyc_universe_v2`
3. `non_nyc_universe_v2`
4. `dscc_targets_v3`
5. `ny_22_scores`

`UNION ALL BY NAME` aligns columns by name and retains all records, including duplicates, before deduplication. The `deduped` CTE retains one row per `person_id` and prefers records where `support_score_v1` is non-null. If several rows share that priority, selection is not deterministic because there is no secondary sort key.

The DCCC, DSCC, and NY-22 source tables are included as supplied; their underlying eligibility criteria are not established by the SQL shown in this README.

## 5. Final Exclusions

After deduplication, the combined universe applies the following rules to **all** included source groups.

### Negative contact results

Exclude voters with a matching contact record on or after **June 1, 2025** with any of these results:

- `Refused`
- `Do Not Call`
- `Disconnected`
- `Hostile`
- `Refused Contact`
- `Threatened`

Records are matched using `person_id` in the contact history table.

### Recent canvassing

Exclude voters with a contact result of `Canvassed` during the previous **three days (rolling 72 hours)**. The window is evaluated relative to `CURRENT_TIMESTAMP()` each time the table is rebuilt.

### High-support/high-turnout exclusions

Exclude voters meeting **either** combination:

| Support measure | Support threshold | Turnout threshold |
|---|---|---|
| Governor support | ≥ 90 | ≥ 90 |
| DNC support | ≥ 90 | ≥ 90 |

Both thresholds in the same row must be met. Missing scores are replaced with `-1` for these comparisons, so a missing score does not trigger the exclusion.

### Opposition survey responses

Exclude voters with a matching `myv_van_id` and survey response of `4 - Lean Oppose` or `5 - Strong Oppose`. The final survey exclusion has no date restriction.

## 6. How Scores Are Used

Scores are **not averaged or added** to create a single composite score. Instead, each input universe uses specific thresholds to determine eligibility:

- **Governor support** identifies higher-support voters in NYC/Buffalo/Rochester and moderate-support voters in one non-NYC group and selected districts.
- **Governor persuasion** is used with higher turnout in the second non-NYC group (persuasion ≥ 40; turnout ≥ 80).
- **Clarity turnout** defines distinct targeting bands, including 30–70 in the NYC/Buffalo/Rochester universe and 30–65 in DCCC district universes.
- **DCCC CSM** is used for congressional-district eligibility where its union source is enabled.
- **DNC support** is used in the final combined universe to exclude voters with both high DNC support and high modeled turnout.

The union operation brings these different eligibility strategies together; the final exclusions are then applied consistently across the resulting dataset.

## 7. Execution Order

1. Build `base_universe` from the voter file and scoring tables.
2. Build `nyc_universe_v2` and `non_nyc_universe_v2`.
3. Build any district-specific universes that will be used.
4. Ensure `Xtmp_dccc`, `dscc_targets_v3`, and `ny_22_scores` are available and refreshed as appropriate.
5. Run `combined_universe_v2` to union sources, deduplicate voters, and apply final exclusions.
6. Use the resulting table as the Phase 2 direct voter contact universe.

## 8. Operational Notes

- **Refresh sensitivity:** The rolling canvassing exclusion and updates to scores, contacts, or survey responses can change membership on subsequent runs.
- **Deduplication:** Consider a stable secondary ordering when multiple source rows have non-null support scores.
- **Source-table alignment:** `UNION ALL BY NAME` expects compatible column names and types across included tables.
- **Potential join duplication:** District-specific score tables are not independently deduplicated in the supplied scripts.
- **Geographic scope:** The non-NYC script does not exclude Richmond County, even though the NYC script includes it.
- **Active sources:** Commented-out district tables are not included in the final combined table.
- **Score interpretation:** Verify the scale, target outcome, and calibration of each model before comparing scores across different models.

## Final Output

`project.dataset.combined_universe` is the **master Phase 2 direct voter contact universe**. It consolidates the enabled targeting datasets, retains one selected record per voter, and applies shared contact-history, survey-response, and score-based exclusions before use in direct voter contact operations.
