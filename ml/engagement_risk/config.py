"""Frozen definitions from notebooks/engagement_risk_feasibility_v1.ipynb. Changing anything here changes the
model; bump MODEL_VERSION (and the artifact prefix) when that is intended."""

import json
from datetime import date, timedelta
from pathlib import Path

MODEL_NAME = "engagement-risk"
ARTIFACT_VERSION = "v1"
MODEL_VERSION = f"{MODEL_NAME}-{ARTIFACT_VERSION}"

# Team account only; the Bedrock profile is another account and is never used here.
TEAM_ACCOUNT = "962450756990"
REGION = "us-east-2"
DEFAULT_PROFILE = "cuy-loyalty"
SILVER_DB = "cuy_loyalty_dev_silver"
GOLD_DB = "cuy_loyalty_dev_gold"
ARTIFACTS_BUCKET = "cuy-loyalty-dev-962450756990-artifacts"
ARTIFACTS_PREFIX = f"ml/{MODEL_NAME}/{ARTIFACT_VERSION}/"
GOLD_TABLE = "engagement_risk_scores"

TARGET_NAME = "low_engagement_next_90d"
TARGET_DEFINITION = (
    "1 if the customer makes fewer than 2 eligible transactions in [T, T + 90 days), else 0. Eligible: approved, "
    "customer-initiated Purchase, Payment, Transfer, Withdrawal or Deposit (not Adjustment)."
)
POPULATION_DEFINITION = "registered before T and at least 1 eligible transaction in [T - 365 days, T)"
ELIGIBLE_TYPES = ("Purchase", "Payment", "Transfer", "Withdrawal", "Deposit")
LOW_ENGAGEMENT_BELOW = 2  # fewer than this many eligible transactions in the horizon = 1
HORIZON_DAYS = 90
POPULATION_LOOKBACK_DAYS = 365
WINDOWS = (30, 90, 180)
RECENCY_CAP_DAYS = 999  # unknown click / login recency

# Rows gold already leaves out (EXCLUDE_WHEN in data/pipelines/glue/silver_to_gold.py).
EXCLUDE = {
    "transactions": ["orphan_customers", "orphan_products", "outside_dataset"],
    "products": ["orphan_customers"],
    "call_center_interactions": ["orphan_customers", "outside_dataset"],
    "complaints": ["orphan_customers", "outside_dataset"],
    "campaign_sends": ["orphan_customers", "outside_dataset"],
}

# A snapshot T is an exclusive midnight cutoff: features use events dated before T, labels [T, T + 90d).
# as_of_date = T - 1 day is the last calendar day the features include.
NOTEBOOK_SPLITS = {
    "train": [date(2024, 7, 1), date(2024, 10, 1), date(2025, 1, 1), date(2025, 4, 1)],
    "validation": [date(2025, 7, 1), date(2025, 10, 1)],
    "test": [date(2026, 1, 1)],
}
# Final fit: every labelled quarterly snapshot whose label window closes before the serving date. The test
# snapshot is included only after the independent evaluation in the notebook was completed and frozen.
FINAL_TRAINING_SNAPSHOTS = NOTEBOOK_SPLITS["train"] + NOTEBOOK_SPLITS["validation"] + NOTEBOOK_SPLITS["test"]
SERVING_AS_OF = date(2026, 6, 17)  # last complete business date in the source data


def cutoff_for(as_of: date) -> date:
    """The exclusive cutoff T for features as of a calendar day."""
    return as_of + timedelta(days=1)


# Model input columns, in the exact order the model was trained with.
FEATURES = [
    "txn_count_30d", "spend_usd_30d", "txn_count_90d", "spend_usd_90d", "txn_count_180d", "spend_usd_180d",
    "active_days_90d", "active_days_180d", "days_since_last_txn", "n_txn_types_180d", "n_categories_180d",
    "count_trend_30d_vs_prior60d", "count_ratio_90d_vs_prior90d", "txns_per_active_day_180d", "declined_count_90d",
    "tenure_days", "products_opened", "sends_180d", "opens_180d", "clicks_180d", "days_since_last_click",
    "contacts_180d", "complaints_180d", "logins_90d", "digital_active_days_90d", "days_since_last_login",
]

# Selected on validation PR-AUC in the notebook (section 11); 77 trees from early stopping. Not re-tuned.
LGBM_PARAMS = {
    "objective": "binary", "metric": "average_precision", "n_estimators": 77, "learning_rate": 0.05,
    "num_leaves": 127, "max_depth": 6, "min_child_samples": 50, "subsample": 1.0, "subsample_freq": 1,
    "colsample_bytree": 0.8, "reg_alpha": 0.1, "reg_lambda": 1.0, "random_state": 7, "deterministic": True,
    "force_col_wise": True, "n_jobs": 8, "verbose": -1,
}

# Serving rules.
NEW_CUSTOMER_TENURE_DAYS = 90
TIER_HIGH_SHARE = 0.20  # top 20 % of model scores: the validated loyalty targeting capacity
TIER_MEDIUM_SHARE = 0.30  # the next 30 % (50th-80th percentile); the remaining 50 % are low

REPORTED_METRICS_PATH = Path(__file__).with_name("reported_metrics.json")


def reported_metrics() -> dict:
    """Validation / test results measured in the notebook (the independent benchmark, never recomputed)."""
    return json.loads(REPORTED_METRICS_PATH.read_text(encoding="utf-8"))
