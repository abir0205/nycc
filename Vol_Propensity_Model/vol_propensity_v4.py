"""
DVC volunteer propensity model.

Source: project.dataset.ml_features_table_v5  (one row per person, VAN/voter-file fields)
Label:  dvc (1 = worked a DVC shift)

`project` and `dataset` are placeholders. Set SOURCE_TABLE and the
SCORES_TABLE_* constants below to your own BigQuery tables.

Setup:
    pip install xgboost scikit-learn pandas numpy joblib db-dtypes \
        google-cloud-bigquery shap matplotlib
    Put a service-account key at resources/google_creds.json (BigQuery
    Job User, plus Data Editor on the output dataset if UPLOAD_SCORES).
    Never commit that key or ./output -- the scores file holds VAN ids.

Usage:
    python vol_propensity.py

Outputs (written to ./output):
    dvc_model.json          trained booster (on the feature set that shipped)
    dvc_calibrator.joblib   score -> est_chance mapping, for scoring new people
    dvc_scores.csv          vanid, score, est_chance, decile
    feature_importance.csv  SHAP ranking plus how many folds kept each feature
    metrics.json            full-model, selected-model and ablation metrics
    shap_summary.png        beeswarm plot (needs shap + matplotlib)

    and, if UPLOAD_SCORES, the same scores to BigQuery:
    project.dataset.dvc_scores_latest    replaced every run
    project.dataset.dvc_scores_history   appended every run, by scored_at

Read before trusting any number this prints
-------------------------------------------
1. Rows that failed the voter-file join have NULL across the whole voter-file
   block and no myv_van_id, so they cannot be handed to an organizer. They
   are dropped by default; set DROP_UNMATCHED = False to keep them with an
   explicit vf_matched flag instead.

2. myv_van_id is not unique in the raw pull. Duplicates put the same person
   on both sides of a fold and inflate every metric, so they are collapsed
   before splitting.

3. vol_yes (and vol_response_tier, its graded version) carry most of the
   signal. If they are written at or after recruitment they are
   contemporaneous with the label. has_email and matched_analytics have the
   same open question. Each set has its own ablation (ABLATIONS) -- treat the
   gap as the cost of depending on fields whose timing you have not
   verified. Feature selection cannot catch this: a leaking field has high
   importance, so selection keeps it.

4. The candidate list (FEATURES) is checked against the table's schema at
   startup. Columns missing from the table are reported and skipped, so a
   schema change shows up in the log instead of as a failed query.

Feature selection
-----------------
Selection happens inside each CV fold, on that fold's training rows only
(Boruta-style shadow features: every column gets a shuffled copy, and a real
feature must clearly out-rank its own shuffled copy to stay). The selected CV
therefore grades "select features, then train" honestly, rather than grading
a feature set that was chosen by looking at the test rows.

Correlated features are judged as groups (FEATURE_GROUPS), so a block like
vote history is kept or dropped as a unit instead of losing members one at a
time while they split credit among themselves.

The selected model ships only if it scores within one fold sd of the
full-feature model on both PR-AUC and precision@k. Otherwise the full model
ships. The final feature set is whatever was kept in at least
STABLE_MIN_FOLDS of the folds.

Reading the scores
------------------
score is the model's raw output. Because training weights each volunteer
~14x (scale_pos_weight), it runs high: 0.5 is roughly the average person, not
a coin flip. It is right for ranking, wrong as a percentage.

est_chance is the calibrated estimate of the real-world chance that the
person has worked a DVC shift. It is learned from the out-of-fold scores with
isotonic regression, cross-fitted so each fold is calibrated on the others.
The decile table prints est_chance next to the actual volunteer rate so you
can check it each run. For prospects it is the chance of resembling a past
volunteer; the yes rate when called is only known once outreach results are
tracked against dvc_scores_history.

Evaluation notes
----------------
Early stopping uses an inner split carved out of each training fold, so
rounds are never chosen on the rows being scored. Every reported metric,
the decile table, the county audit and the deliverable scores are
out-of-fold.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from google.cloud import bigquery
from google.oauth2 import service_account
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, train_test_split

warnings.filterwarnings("ignore", message="BigQuery Storage module not found")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HERE = Path(__file__).parent
KEY_FILE = HERE / "resources" / "google_creds.json"
OUTPUT_DIR = HERE / "output"

SOURCE_TABLE = "project.dataset.ml_features_table_v5"
LABEL = "label"
ID_COL = "myv_van_id"
MATCH_COL = "vf_matched"

RANDOM_STATE = 42
N_FOLDS = 5

DROP_UNMATCHED = True

# Each entry refits without that set of columns, to size how much of the
# model rests on fields whose timing is unverified. Columns not present in
# the table are ignored.
RUN_ABLATION = True
ABLATIONS = {
    "stated_interest": ["vol_yes", "vol_response_tier"],
    "contact_records": ["has_email", "matched_analytics"],
}

K_FRAC = 0.005
K_LABEL = f"top{K_FRAC:.1%}"

ES_FRAC = 0.15
ES_ROUNDS = 100
SHAP_SAMPLE = 20_000

# --- upload to BigQuery ----------------------------------------------------
# Scores go straight from the DataFrame to BigQuery (no CSV round trip).
# _latest is replaced on every run and is what organizers and reports query.
# _history gets every run appended, stamped with scored_at, so you can later
# check who the model ranked highly against who actually volunteered.
# The service account needs BigQuery Data Editor on the target dataset.
#
# myv_van_id is uploaded as STRING. If an existing history table stores it
# as INT64, the append will fail on the type change -- use new table names
# or drop the old history table.
UPLOAD_SCORES = True
KEEP_HISTORY = True
SCORES_TABLE_LATEST = "project.dataset.dvc_scores_latest"
SCORES_TABLE_HISTORY = "project.dataset.dvc_scores_history"

# --- feature selection -----------------------------------------------------
SELECT_FEATURES = True

# Shadow fits per fold. A feature (or group) stays if its importance beats
# SHADOW_MARGIN x its own shuffled copy in a strict majority of them. More
# repeats = steadier decisions, slower.
SHADOW_REPEATS = 3
SHADOW_MARGIN = 1.5

# Shadow fits use every positive and at most this many negatives. The
# decision only needs a ranking, not the full file.
SHADOW_MAX_NEGATIVES = 200_000

# Features judged together. Columns not listed here are judged on their own.
# Members missing from the table are ignored.
FEATURE_GROUPS = {
    "vote_history": [
        "general_votes_last3",
        "offyear_votes_last3",
        "primary_dem_votes_last5",
        "voted_early_last_general",
        "longest_gap_years",
        "years_since_earliest_vote",
    ],
    "districts": [
        "us_cong_district_latest",
        "state_senate_district_latest",
        "state_house_district_latest",
    ],
    "stated_interest": ["vol_yes", "vol_response_tier"],
}

# Kept regardless of score. Column or group names.
ALWAYS_KEEP: list[str] = []

# Never select fewer than this many features/groups. The top-up picks the
# units that come closest to passing, so keep this low; it is a safety net.
MIN_UNITS = 3

# A feature/group enters the final model if kept in at least this many folds.
STABLE_MIN_FOLDS = 4

SHADOW_PREFIX = "shadow__"

# --- features --------------------------------------------------------------
# Candidate features, checked against the table at startup. One
# representative per family: age_vf over date_of_birth / age_calculated,
# sex over gender_vf, party_id over party_name_vf, ethnicity_modeled over
# ethnicity_vf, the _latest districts over _old and plain,
# years_since_earliest_vote over earliest_vote_date.
#
# Not candidates:
#   media_market, has_address_on_file, can_contact_call, can_contact_text,
#   open_channel_count -- zero importance in earlier runs.
#   is_do_not_* and can_contact_* -- whether you CAN reach someone, not
#   whether they'd volunteer, and some are set after contact. Use them to
#   filter the call list, not to score.
#   van_precinct_id, voting_zip -- too fine-grained; mostly program reach.
FEATURES = [
    # demographics
    "age_vf",
    "sex",
    "household_size",
    "ethnicity_modeled",
    # geography
    "county_name",
    "us_cong_district_latest",
    "state_senate_district_latest",
    "state_house_district_latest",
    # party and registration
    "party_id",
    "voter_type_id",
    "person_years_since_earliest_reg",
    "years_since_earliest_vote",
    # vote history
    "general_votes_last3",
    "offyear_votes_last3",
    "primary_dem_votes_last5",
    "voted_early_last_general",
    "longest_gap_years",
    # stated interest (see docstring note 3)
    "vol_yes",
    "vol_response_tier",
    # activity and records (timing unverified; see docstring note 3)
    "earliest_activity_type",
    "has_email",
    "matched_analytics",
]

# Strings, and integers that are codes rather than quantities.
CATEGORICAL = [
    "sex",
    "party_id",
    "voter_type_id",
    "county_name",
    "us_cong_district_latest",
    "state_senate_district_latest",
    "state_house_district_latest",
    "ethnicity_modeled",
    "earliest_activity_type",
]

# Never allowed in FEATURES. Label restatements, leaks, and identifiers.
# Checked at startup so one can't slip back in.
FORBIDDEN = [
    "dvc", "shift_count", "dvc_shift_count", "did_volunteer",
    "volunteer_activity_name",                      # label restatements
    "years_since_earliest_activity", "days_since_last_modified",
    "datetime_modified",                            # written when people act
    "myc_van_id", "myv_van_id", "person_id", "email_id",
    "contacts_address_id", "middle_name",           # identifiers
]

LABEL_CHECK_SQL = f"""
SELECT
  dvc,
  COUNT(*)                     AS n,
  COUNTIF(dvc_shift_count > 0) AS has_dvc_shifts,
  COUNTIF(shift_count     > 0) AS has_any_shifts,
  COUNTIF(did_volunteer)       AS did_volunteer
FROM `{SOURCE_TABLE}`
GROUP BY dvc
ORDER BY dvc
"""

MATCH_CHECK_SQL = f"""
SELECT
  NULLIF(myv_van_id, '') IS NULL          AS unmatched,
  COUNT(*)                                AS n,
  COUNTIF(dvc = 1)                        AS positives,
  SAFE_DIVIDE(COUNTIF(dvc = 1), COUNT(*)) AS rate
FROM `{SOURCE_TABLE}`
WHERE NOT COALESCE(is_deceased, FALSE)
  AND dvc IS NOT NULL
GROUP BY unmatched
ORDER BY unmatched
"""

DUPE_CHECK_SQL = f"""
SELECT
  COUNT(*)                              AS rows_with_id,
  COUNT(DISTINCT myv_van_id)            AS distinct_ids,
  COUNT(*) - COUNT(DISTINCT myv_van_id) AS surplus_rows
FROM `{SOURCE_TABLE}`
WHERE NOT COALESCE(is_deceased, FALSE)
  AND dvc IS NOT NULL
  AND NULLIF(myv_van_id, '') IS NOT NULL
"""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def get_client() -> bigquery.Client:
    credentials = service_account.Credentials.from_service_account_file(
        KEY_FILE,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(credentials=credentials, project=credentials.project_id)


def resolve_features(client: bigquery.Client) -> list[str]:
    """Check the candidate list against the table and return what exists."""
    bad = sorted(set(FEATURES) & set(FORBIDDEN))
    if bad:
        raise ValueError(f"forbidden columns in FEATURES: {bad}")

    present = {f.name for f in client.get_table(SOURCE_TABLE).schema}

    required = {ID_COL, "dvc", "is_deceased"}
    if not required <= present:
        raise ValueError(f"{SOURCE_TABLE} is missing {sorted(required - present)}")

    missing = [c for c in FEATURES if c not in present]
    if missing:
        print(f"\nnot in {SOURCE_TABLE}, skipped ({len(missing)}):")
        for c in missing:
            print(f"  {c}")
    return [c for c in FEATURES if c in present]


def feature_sql(cols: list[str]) -> str:
    # COALESCE on is_deceased: NOT NULL evaluates to NULL, which silently
    # drops every row with an unknown deceased flag. dvc IS NOT NULL keeps a
    # null label from becoming a NaN target.
    select = ",\n  ".join(cols)
    return f"""
SELECT
  NULLIF(myv_van_id, '') AS myv_van_id,
  CAST(dvc AS INT64) AS label,
  {select}
FROM `{SOURCE_TABLE}`
WHERE NOT COALESCE(is_deceased, FALSE)
  AND dvc IS NOT NULL
"""


def preflight(client: bigquery.Client) -> None:
    print("label family:")
    print(client.query(LABEL_CHECK_SQL).to_dataframe().to_string(index=False))

    print("\nDVC rate by voter-file join status:")
    match = client.query(MATCH_CHECK_SQL).to_dataframe()
    print(match.to_string(index=False))
    if len(match) == 2:
        spread = abs(float(match.rate.iloc[0]) - float(match.rate.iloc[1]))
        if spread > 0.01:
            print(
                f"  rate differs by {spread:.1%} across join status.\n"
                "  Missing-value splits can encode this, so importance on the\n"
                "  voter-file block is partly join status, not the fields."
            )

    print("\nid uniqueness:")
    print(client.query(DUPE_CHECK_SQL).to_dataframe().to_string(index=False))


def load(client: bigquery.Client, cols: list[str]) -> pd.DataFrame:
    df = client.query(feature_sql(cols)).to_dataframe()
    print(f"\nloaded {len(df):,} raw rows, {df.shape[1]} columns")

    df[ID_COL] = df[ID_COL].astype("string").str.strip()
    matched = df[ID_COL].notna() & (df[ID_COL] != "")
    n_unmatched = int((~matched).sum())
    print(f"  unmatched to voter file: {n_unmatched:,} ({n_unmatched / len(df):.1%})")

    # Dedupe only rows that have an id. Blank ids would otherwise all be
    # treated as one value and collapse into a single row. Stable sort so
    # the same row wins on every run; add a real tiebreaker column in SQL
    # if one exists.
    with_id = (
        df[matched]
        .sort_values(LABEL, ascending=False, kind="stable")
        .drop_duplicates(subset=[ID_COL])
    )
    dropped = int(matched.sum()) - len(with_id)
    if dropped:
        print(f"  collapsed {dropped:,} duplicate ids")

    if DROP_UNMATCHED:
        df = with_id
        print(f"  dropped unmatched -> {len(df):,} rows")
    else:
        df = pd.concat([with_id, df[~matched]])
        df[MATCH_COL] = df[ID_COL].notna().astype("float32")
        print("  unmatched kept, exposed as vf_matched")

    df = df.reset_index(drop=True)
    print(f"\nmodeling on {len(df):,} rows")
    return df


def audit(df: pd.DataFrame) -> None:
    n_pos = int(df[LABEL].sum())
    print(f"\npositives: {n_pos:,} of {len(df):,}  ({df[LABEL].mean():.2%})")
    if n_pos < 1000:
        print(
            "  under 1,000 positives -- cut to 10-15 features, raise\n"
            "  min_child_weight, and treat fold-to-fold variance as real"
        )

    nulls = df.isna().mean().sort_values(ascending=False)
    hot = nulls[nulls > 0.05]
    print(f"\nnull rate > 5% ({len(hot)} columns):")
    for col, pct in hot.items():
        print(f"  {col:<35} {pct:.1%}")
    if hot.empty:
        print("  none")
    else:
        clusters = hot.round(4).value_counts()
        for rate, count in clusters[clusters > 2].items():
            cols = hot[hot.round(4) == rate].index.tolist()
            print(
                f"\n  {count} columns share a {rate:.1%} null rate -- one"
                f" upstream\n  join, not independent gaps: {', '.join(cols[:4])}..."
            )

    print("\nnear-constant (>98% one value):")
    flagged = False
    for col in df.columns:
        if col in (ID_COL, LABEL):
            continue
        share = df[col].value_counts(normalize=True, dropna=False).iloc[0]
        if share > 0.98:
            print(f"  {col:<35} {share:.1%}  -> consider dropping")
            flagged = True
    if not flagged:
        print("  none")

    print("\ncategory levels:")
    for col in [c for c in CATEGORICAL if c in df.columns]:
        print(f"  {col:<35} {df[col].nunique()}")


def to_float(s: pd.Series) -> np.ndarray:
    """Numbers and BOOLs (with or without blanks) to float32, blanks as NaN."""
    if s.dtype == object:
        s = s.map(lambda v: float(v) if isinstance(v, (bool, np.bool_)) else v)
    s = pd.to_numeric(s, errors="coerce")
    return s.astype("Float64").to_numpy(dtype="float32", na_value=np.nan)


def prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    X = df.drop(columns=[c for c in (ID_COL, LABEL) if c in df.columns])
    y = df[LABEL].astype(int)

    for col in X.columns:
        if col in CATEGORICAL:
            # Strings first, so an integer code like voter_type_id becomes a
            # set of labels rather than a number the model can order.
            X[col] = X[col].astype("string").astype("category")
        else:
            X[col] = to_float(X[col])

    # A numeric column that came out entirely blank was text (or a DATE) that
    # belonged in CATEGORICAL or needed converting in SQL. Fail instead of
    # silently fitting on nothing.
    empty = [c for c in X.columns if c not in CATEGORICAL and X[c].isna().all()]
    if empty:
        raise ValueError(
            f"all-NaN after numeric conversion: {empty}. Add them to "
            "CATEGORICAL, convert dates in SQL, or remove them from FEATURES."
        )

    print(f"\nfitting on {X.shape[1]} features, {len(X):,} rows")
    return X, y


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def top_k(n: int) -> int:
    return max(1, int(round(K_FRAC * n)))


def precision_at_k(y_true, y_score) -> float:
    k = top_k(len(y_score))
    idx = np.argsort(y_score)[::-1][:k]
    return float(np.asarray(y_true)[idx].mean())


def evaluate(y_true, y_score) -> dict:
    base = np.asarray(y_true).mean()
    prec = precision_at_k(y_true, y_score)
    return {
        "roc_auc": float(roc_auc_score(y_true, y_score)),
        "pr_auc": float(average_precision_score(y_true, y_score)),
        f"precision@{K_LABEL}": prec,
        f"lift@{K_LABEL}": prec / base if base else float("nan"),
    }


def show(name: str, metrics: dict, spread: dict | None = None) -> None:
    print(f"\n{name}:")
    for key, val in metrics.items():
        sd = f"   (fold sd {spread[key]:.4f})" if spread else ""
        print(f"  {key:<24} {val:.4f}{sd}")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def make_model(scale_pos_weight: float, **overrides) -> xgb.XGBClassifier:
    params = dict(
        objective="binary:logistic",
        eval_metric="aucpr",
        enable_categorical=True,
        tree_method="hist",
        max_cat_to_onehot=1,
        n_estimators=2000,
        early_stopping_rounds=ES_ROUNDS,
        learning_rate=0.03,
        max_depth=5,
        min_child_weight=20,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_lambda=2.0,
        scale_pos_weight=scale_pos_weight,
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    params.update(overrides)
    return xgb.XGBClassifier(**params)


def pos_weight(y: pd.Series) -> float:
    return (y == 0).sum() / max((y == 1).sum(), 1)


def fit_with_inner_es(
    X_train: pd.DataFrame, y_train: pd.Series, seed: int = RANDOM_STATE
) -> tuple[xgb.XGBClassifier, pd.DataFrame]:
    """Fit with early stopping on a slice of the training data. Returns the
    model and that slice, which the selection step reuses as unseen rows."""
    X_fit, X_es, y_fit, y_es = train_test_split(
        X_train, y_train, test_size=ES_FRAC, stratify=y_train, random_state=seed
    )
    model = make_model(pos_weight(y_fit))
    model.fit(X_fit, y_fit, eval_set=[(X_es, y_es)], verbose=False)
    return model, X_es


def shap_matrix(
    model: xgb.XGBClassifier, sample: pd.DataFrame, n_rounds: int | None = None
) -> np.ndarray:
    """Per-row SHAP contributions, counting only the kept trees."""
    dmatrix = xgb.DMatrix(sample, enable_categorical=True)
    kwargs = {"iteration_range": (0, n_rounds)} if n_rounds else {}
    contribs = model.get_booster().predict(dmatrix, pred_contribs=True, **kwargs)
    return contribs[:, :-1]  # trailing column is the bias term


# ---------------------------------------------------------------------------
# Feature selection (shadow features, inside each fold)
# ---------------------------------------------------------------------------


def build_units(cols: list[str]) -> dict[str, list[str]]:
    """Map each selection unit (a group or a single column) to its columns."""
    units, grouped = {}, set()
    for name, members in FEATURE_GROUPS.items():
        present = [c for c in members if c in cols]
        if present:
            units[name] = present
            grouped.update(present)
    for c in cols:
        if c not in grouped:
            units[c] = [c]
    return units


def unit_of(col: str, units: dict[str, list[str]]) -> str | None:
    for name, members in units.items():
        if col == name or col in members:
            return name
    return None


def expand(unit_names, units: dict[str, list[str]]) -> list[str]:
    return [c for u in unit_names for c in units[u]]


def cap_negatives(
    X: pd.DataFrame, y: pd.Series, seed: int
) -> tuple[pd.DataFrame, pd.Series]:
    pos = y.index[y == 1]
    neg = y.index[y == 0]
    if len(neg) > SHADOW_MAX_NEGATIVES:
        rng = np.random.default_rng(seed)
        neg = pd.Index(rng.choice(neg.values, SHADOW_MAX_NEGATIVES, replace=False))
    keep = pos.union(neg)
    return X.loc[keep], y.loc[keep]


def add_shadows(X: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Append a shuffled copy of every column. Shuffling keeps each column's
    values and blank rate but breaks any link to the label, so a shadow's
    importance is what pure chance earns for a column like that one."""
    shadows = {}
    for col in X.columns:
        shuffled = X[col].iloc[rng.permutation(len(X))]
        shuffled.index = X.index
        shadows[SHADOW_PREFIX + col] = shuffled
    out = pd.concat([X, pd.DataFrame(shadows, index=X.index)], axis=1)

    # Shuffle column order too. When two columns offer near-identical splits
    # (common with high-cardinality categories like county), XGBoost favors
    # whichever comes first. With real columns always first, noise features
    # would beat their own shadows every time and never get dropped.
    return out[list(rng.permutation(out.columns))]


def unit_importance(
    contribs: np.ndarray,
    cols: list[str],
    units: dict[str, list[str]],
    prefix: str = "",
) -> dict[str, float]:
    """Mean |SHAP| per unit. SHAP values add up, so a group's contribution
    for one person is the sum of its members' contributions."""
    idx = {c: i for i, c in enumerate(cols)}
    return {
        name: float(
            np.abs(contribs[:, [idx[prefix + c] for c in members]].sum(axis=1)).mean()
        )
        for name, members in units.items()
    }


def select_units(X_tr: pd.DataFrame, y_tr: pd.Series, seed: int) -> list[str]:
    """Pick the units that clearly beat their own shadow, training rows only."""
    units = build_units(list(X_tr.columns))
    always = {unit_of(c, units) for c in ALWAYS_KEEP} - {None}

    X_s, y_s = cap_negatives(X_tr, y_tr, seed)
    rng = np.random.default_rng(seed)
    wins = dict.fromkeys(units, 0)
    total = dict.fromkeys(units, 0.0)
    shadow_total = dict.fromkeys(units, 0.0)

    for r in range(SHADOW_REPEATS):
        augmented = add_shadows(X_s, rng)
        model, X_es = fit_with_inner_es(augmented, y_s, seed=seed + r)
        sample = X_es.sample(min(SHAP_SAMPLE, len(X_es)), random_state=seed + r)
        contribs = shap_matrix(model, sample, model.best_iteration + 1)
        cols = list(augmented.columns)

        real = unit_importance(contribs, cols, units)
        fake = unit_importance(contribs, cols, units, SHADOW_PREFIX)
        for u in units:
            # Each unit is judged against its OWN shadow, not the best shadow
            # overall. A shuffled high-cardinality category can partition its
            # way into a lot of chance importance; using that as everyone's
            # bar hides real but modest numeric signals like age.
            wins[u] += real[u] > 0 and real[u] > SHADOW_MARGIN * fake[u]
            total[u] += real[u]
            shadow_total[u] += fake[u]

    # Strict majority: a noise feature sits right at its own shadow and wins
    # about half the time, so ">= half" would keep it too often.
    keep = [u for u in units if wins[u] > SHADOW_REPEATS / 2 or u in always]

    # Top up to the floor by how far each unit beats its own shadow, not by
    # raw importance -- raw importance favors high-cardinality noise.
    if len(keep) < MIN_UNITS:
        ratio = {u: total[u] / (shadow_total[u] + 1e-12) for u in units}
        for u in sorted(units, key=ratio.get, reverse=True):
            if u not in keep:
                keep.append(u)
            if len(keep) >= MIN_UNITS:
                break
    return keep


# ---------------------------------------------------------------------------
# Cross-validation
# ---------------------------------------------------------------------------


def cross_validate(
    X: pd.DataFrame,
    y: pd.Series,
    skf: StratifiedKFold,
    label: str,
    select: bool = False,
) -> dict:
    """Out-of-fold metrics, fold spread, OOF scores and median tree count.
    With select=True, each fold picks its own features from its own training
    rows before fitting, and the result records what each fold kept."""
    oof = np.zeros(len(y))
    fold_of = np.zeros(len(y), dtype=int)
    rounds, per_fold, fold_units = [], [], []
    units = build_units(list(X.columns))

    for fold, (tr, va) in enumerate(skf.split(X, y), 1):
        X_tr, y_tr = X.iloc[tr], y.iloc[tr]

        if select:
            kept = select_units(X_tr, y_tr, seed=RANDOM_STATE + fold)
            fold_units.append(kept)
            cols = expand(kept, units)
        else:
            cols = list(X.columns)

        model, _ = fit_with_inner_es(X_tr[cols], y_tr)
        oof[va] = model.predict_proba(X.iloc[va][cols])[:, 1]
        fold_of[va] = fold
        rounds.append(model.best_iteration + 1)
        per_fold.append(evaluate(y.iloc[va].values, oof[va]))

        note = f"  kept {len(cols)}/{X.shape[1]} features" if select else ""
        print(f"  fold {fold}  trees={model.best_iteration + 1}{note}")

    metrics = evaluate(y.values, oof)
    spread = {k: float(np.std([f[k] for f in per_fold])) for k in metrics}
    show(label, metrics, spread)

    if metrics["roc_auc"] > 0.90:
        print(
            "\n  NOTE: ROC-AUC above 0.90 on this kind of data usually means\n"
            "  leakage rather than skill. Check what's still in the features."
        )

    median_rounds = int(np.median(rounds))
    if max(rounds) - min(rounds) > 3 * max(median_rounds, 1):
        print(
            f"  tree count spread {min(rounds)}-{max(rounds)} is wide; folds"
            " are landing\n  on different models, so fold variance is real."
        )

    return {
        "metrics": metrics,
        "fold_sd": spread,
        "oof": oof,
        "fold_of": fold_of,
        "median_rounds": median_rounds,
        "fold_units": fold_units,
    }


def ablate(
    X: pd.DataFrame, y: pd.Series, skf: StratifiedKFold, baseline: dict
) -> dict:
    """Refit without each set of timing-unverified fields. If most of the
    lift disappears, the model rests on those fields, and their timing needs
    verifying before anyone targets on these scores."""
    results = {}
    key = f"lift@{K_LABEL}"
    for name, cols_out in ABLATIONS.items():
        removed = [c for c in cols_out if c in X.columns]
        if not removed:
            print(f"\n  {name}: none of {cols_out} in the data, skipped")
            continue

        print(f"\nablating {name} ({', '.join(removed)})...")
        keep = [c for c in X.columns if c not in removed]
        res = cross_validate(X[keep], y, skf, label=f"without {name}")
        drop = baseline[key] - res["metrics"][key]
        print(f"  {key} change: {-drop:+.4f}")
        if drop > 0.5 * baseline[key]:
            print(
                "  More than half the lift rests on these fields. Verify when"
                " they are\n  written before anyone targets on these scores."
            )
        results[name] = {
            "removed": removed,
            "metrics": res["metrics"],
            "fold_sd": res["fold_sd"],
        }
    return results


def stable_units(fold_units: list[list[str]]) -> tuple[list[str], dict[str, int]]:
    counts: dict[str, int] = {}
    for kept in fold_units:
        for u in kept:
            counts[u] = counts.get(u, 0) + 1
    stable = [u for u, n in counts.items() if n >= STABLE_MIN_FOLDS]
    return stable, counts


def selection_passes(full: dict, selected: dict) -> bool:
    """Selected model ships only if it is within one fold sd of the full
    model on both PR-AUC and precision at the top of the list."""
    ok = True
    for key in ("pr_auc", f"precision@{K_LABEL}"):
        floor = full["metrics"][key] - full["fold_sd"][key]
        passed = selected["metrics"][key] >= floor
        ok &= passed
        print(
            f"  {key:<24} selected {selected['metrics'][key]:.4f}"
            f"  vs floor {floor:.4f}  {'ok' if passed else 'FAIL'}"
        )
    return ok


# ---------------------------------------------------------------------------
# Final model, importance report, diagnostics, scoring
# ---------------------------------------------------------------------------


def fit_full(X: pd.DataFrame, y: pd.Series, n_rounds: int) -> xgb.XGBClassifier:
    """Refit on everything at a fixed tree count, for scoring people added
    later. No metric is read off this model. Rounds scaled up because CV fits
    saw ~68% of the data (0.8 x 0.85)."""
    model = make_model(
        pos_weight(y),
        n_estimators=max(1, int(n_rounds * 1.2)),
        early_stopping_rounds=None,
    )
    model.fit(X, y, verbose=False)
    return model


def importance_report(
    model: xgb.XGBClassifier,
    X: pd.DataFrame,
    all_cols: list[str],
    counts: dict[str, int],
) -> pd.DataFrame:
    """SHAP ranking for the shipped model, plus selection stability for every
    candidate feature. Explanatory only -- nothing downstream uses it."""
    sample = X.sample(min(SHAP_SAMPLE, len(X)), random_state=RANDOM_STATE)
    values = shap_matrix(model, sample)
    shap_by_col = dict(zip(X.columns, np.abs(values).mean(axis=0)))

    units = build_units(all_cols)
    table = pd.DataFrame(
        {
            "feature": all_cols,
            "unit": [unit_of(c, units) for c in all_cols],
            "mean_abs_shap": [shap_by_col.get(c, 0.0) for c in all_cols],
            "in_final_model": [c in X.columns for c in all_cols],
        }
    )
    table["folds_kept"] = table.unit.map(counts).fillna(0).astype(int)
    table = table.sort_values("mean_abs_shap", ascending=False).reset_index(drop=True)

    print("\nfeature importance (shipped model):")
    print(table.to_string(index=False))

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import shap

        shap.summary_plot(values, sample, show=False, max_display=25)
        plt.tight_layout()
        plt.savefig(OUTPUT_DIR / "shap_summary.png", dpi=150, bbox_inches="tight")
        plt.close()
    except ImportError:
        print("  (plot skipped: pip install shap matplotlib)")
    except Exception as exc:
        print(f"  (plot skipped: {exc})")

    return table


def _isotonic() -> IsotonicRegression:
    return IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")


def calibrate(
    oof: np.ndarray, y: pd.Series, fold_of: np.ndarray
) -> tuple[np.ndarray, IsotonicRegression]:
    """Turn raw scores into estimated real chances.

    Isotonic regression learns a step-shaped, never-decreasing mapping from
    score to actual volunteer rate, so it fixes both the class weighting and
    the model's squeeze toward the middle without changing anyone's rank
    (people close in score can end up tied).

    Returns two things. The cross-fitted estimates (each fold mapped by a
    calibrator trained on the other folds) are for the check in the decile
    table, so it isn't the calibrator grading itself. The calibrator fit on
    all out-of-fold scores produces the delivered est_chance: one mapping for
    everyone, so est_chance never runs against the score order.
    """
    labels = y.values
    est = np.zeros(len(oof))
    for f in np.unique(fold_of):
        mask = fold_of == f
        est[mask] = _isotonic().fit(oof[~mask], labels[~mask]).predict(oof[mask])
    return est, _isotonic().fit(oof, labels)


def diagnostics(
    oof: np.ndarray, est: np.ndarray, df: pd.DataFrame, y: pd.Series
) -> None:
    frame = pd.DataFrame({"score": oof, "est_chance": est, "label": y.values})
    frame["decile"] = pd.qcut(
        frame.score.rank(method="first"), 10, labels=range(10, 0, -1)
    ).astype(int)

    # est_chance should sit close to rate in every decile. It is cross-fitted,
    # so a close match here is a real check, not the calibrator grading
    # itself.
    print("\ndecile lift and calibration check (out-of-fold):")
    print(
        frame.groupby("decile")
        .agg(
            n=("label", "size"),
            positives=("label", "sum"),
            rate=("label", "mean"),
            est_chance=("est_chance", "mean"),
        )
        .assign(lift=lambda d: d.rate / y.mean())
        .to_string()
    )

    if "county_name" in df.columns:
        county = (
            frame.assign(county=df.county_name.values)
            .groupby("county", observed=True)
            .agg(
                n=("score", "size"),
                mean_score=("score", "mean"),
                actual_rate=("label", "mean"),
            )
            .sort_values("mean_score", ascending=False)
        )
        print("\nout-of-fold score by county:")
        print(county.to_string())
        print(
            "\n  Counties scored well below their own outcome rate are where"
            " the\n  program under-organized, not where people are less"
            " willing."
        )


def score_file(
    oof: np.ndarray, est: np.ndarray, df: pd.DataFrame, y: pd.Series
) -> pd.DataFrame:
    """Out-of-fold scores: each id scored by a model that never saw it.
    Deciles only for people who haven't volunteered yet (1 = best)."""
    assert len(oof) == len(df) == len(y), "oof, df and y must be row-aligned"
    scores = pd.DataFrame(
        {
            ID_COL: df[ID_COL].values,
            "score": oof,
            "est_chance": est,
            "already_volunteered": y.values.astype(bool),
        }
    )
    scores = scores[scores[ID_COL].notna()].copy()

    prospects = ~scores["already_volunteered"]
    scores.loc[prospects, "decile"] = pd.qcut(
        scores.loc[prospects, "score"].rank(method="first"),
        10,
        labels=range(10, 0, -1),
    ).astype(int)
    scores["decile"] = scores["decile"].astype("Int64")
    return scores.sort_values("score", ascending=False)


def upload_scores(
    client: bigquery.Client, scores: pd.DataFrame, run_info: dict
) -> None:
    """Write the deliverable scores to BigQuery, with run metadata."""
    out = scores.copy()
    out[ID_COL] = out[ID_COL].astype("string")  # STRING, matching the source
    out["decile"] = out["decile"].astype("Int64")
    out["already_volunteered"] = out["already_volunteered"].astype(bool)
    out["scored_at"] = run_info["scored_at"]
    out["feature_set"] = run_info["shipped"]
    out["roc_auc_cv"] = run_info["roc_auc"]
    out["source_table"] = SOURCE_TABLE

    # Cheap checks that would otherwise surface as a bad call list.
    if out.empty:
        raise ValueError("no scores to upload")
    if not out[ID_COL].is_unique:
        raise ValueError("duplicate myv_van_id in scores; refusing to upload")

    schema = [
        bigquery.SchemaField(ID_COL, "STRING", mode="REQUIRED"),
        bigquery.SchemaField("score", "FLOAT64"),  # raw, for ranking
        bigquery.SchemaField("est_chance", "FLOAT64"),  # estimated real chance
        bigquery.SchemaField("already_volunteered", "BOOL"),
        bigquery.SchemaField("decile", "INT64"),  # null for existing volunteers
        bigquery.SchemaField("scored_at", "TIMESTAMP"),
        bigquery.SchemaField("feature_set", "STRING"),
        bigquery.SchemaField("roc_auc_cv", "FLOAT64"),
        bigquery.SchemaField("source_table", "STRING"),
    ]

    targets = [(SCORES_TABLE_LATEST, bigquery.WriteDisposition.WRITE_TRUNCATE, None)]
    if KEEP_HISTORY:
        targets.append(
            (
                SCORES_TABLE_HISTORY,
                bigquery.WriteDisposition.WRITE_APPEND,
                bigquery.TimePartitioning(field="scored_at"),  # one partition per day
            )
        )

    for table, disposition, partitioning in targets:
        config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=disposition,
            time_partitioning=partitioning,
        )
        if disposition == bigquery.WriteDisposition.WRITE_APPEND:
            # Lets a history table created before a column existed accept it.
            # Older rows show NULL for that column.
            config.schema_update_options = [
                bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION
            ]
        job = client.load_table_from_dataframe(out, table, job_config=config)
        job.result()  # waits, and raises if the load failed
        print(f"  uploaded {job.output_rows:,} rows -> {table}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client = get_client()

    feature_cols = resolve_features(client)
    preflight(client)
    df = load(client, feature_cols)
    audit(df)

    X, y = prepare(df)
    assert len(df) == len(X) == len(y), "prepare() dropped or reordered rows"
    all_cols = list(X.columns)
    units = build_units(all_cols)

    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=RANDOM_STATE)

    print("\ncross-validating, all features...")
    full = cross_validate(X, y, skf, label="all features")

    ablation = ablate(X, y, skf, full["metrics"]) if RUN_ABLATION else {}

    shipped, final_cols, counts, selected = full, all_cols, {}, None
    if SELECT_FEATURES:
        print("\ncross-validating with in-fold feature selection...")
        selected = cross_validate(X, y, skf, label="selected features", select=True)

        stable, counts = stable_units(selected["fold_units"])
        print(f"\nselection stability (folds kept, of {N_FOLDS}):")
        for u in units:
            print(f"  {u:<35} {counts.get(u, 0)}")

        print("\nshipping check:")
        if selection_passes(full, selected) and stable:
            shipped, final_cols = selected, expand(stable, units)
            print(f"  -> shipping selected model ({len(final_cols)} features)")
        else:
            print("  -> selected model fell short; shipping all features")

    est_check, calibrator = calibrate(shipped["oof"], y, shipped["fold_of"])
    diagnostics(shipped["oof"], est_check, df, y)
    scores = score_file(shipped["oof"], calibrator.predict(shipped["oof"]), df, y)

    final_model = fit_full(X[final_cols], y, shipped["median_rounds"])
    importance = importance_report(final_model, X[final_cols], all_cols, counts)

    final_model.save_model(OUTPUT_DIR / "dvc_model.json")
    # Map new people's scores through this. The full-data model uses ~20%
    # more trees than the fold models, so treat its est_chance as close,
    # not exact; the next full run recalibrates.
    joblib.dump(calibrator, OUTPUT_DIR / "dvc_calibrator.joblib")
    scores.to_csv(OUTPUT_DIR / "dvc_scores.csv", index=False)
    importance.to_csv(OUTPUT_DIR / "feature_importance.csv", index=False)
    (OUTPUT_DIR / "metrics.json").write_text(
        json.dumps(
            {
                "model": "xgboost",
                "source_table": SOURCE_TABLE,
                "n_rows": len(df),
                "n_positives": int(y.sum()),
                "base_rate": float(y.mean()),
                "k_frac": K_FRAC,
                "dropped_unmatched": DROP_UNMATCHED,
                "candidate_features": all_cols,
                "missing_from_table": [c for c in FEATURES if c not in feature_cols],
                "final_features": final_cols,
                "shipped": "selected" if shipped is selected else "full",
                "final_n_estimators": max(1, int(shipped["median_rounds"] * 1.2)),
                "scores_are_out_of_fold": True,
                # Kept under this key so a baseline comparison (e.g. a
                # logistic-regression script reading this file) compares
                # against the model that actually shipped.
                "cross_validation": shipped["metrics"],
                "cross_validation_fold_sd": shipped["fold_sd"],
                "full_model": {"metrics": full["metrics"], "fold_sd": full["fold_sd"]},
                "selected_model": (
                    {
                        "metrics": selected["metrics"],
                        "fold_sd": selected["fold_sd"],
                        "fold_units": selected["fold_units"],
                        "units_folds_kept": counts,
                    }
                    if selected
                    else None
                ),
                "ablation": ablation,
            },
            indent=2,
        )
    )
    print(f"\nwritten to {OUTPUT_DIR.resolve()}")

    if UPLOAD_SCORES:
        print("\nuploading scores to BigQuery...")
        if shipped["metrics"]["roc_auc"] > 0.90:
            # Same threshold as the leakage warning. Don't overwrite the live
            # table with scores that probably come from a leaking feature.
            print("  skipped: ROC-AUC above 0.90 suggests leakage. Check first.")
        else:
            upload_scores(
                client,
                scores,
                {
                    "scored_at": pd.Timestamp.now(tz="UTC"),
                    "shipped": "selected" if shipped is selected else "full",
                    "roc_auc": shipped["metrics"]["roc_auc"],
                },
            )


if __name__ == "__main__":
    main()