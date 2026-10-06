"""Final fit of the frozen engagement-risk LightGBM, with a replication check against the notebook.

    python -m ml.engagement_risk.train --profile cuy-loyalty

1. Builds the labelled rows for all quarterly snapshots (same population, features and target as the notebook).
2. Replication check: the per-snapshot population sizes must equal the notebook's, and refitting on the 4 train
   snapshots must reproduce the notebook's validation PR-AUC / ROC-AUC. Otherwise nothing is saved.
3. Final fit on FINAL_TRAINING_SNAPSHOTS with the frozen hyperparameters (no re-tuning, same 77 trees).
4. Saves model.txt (LightGBM native), metadata.json, feature_schema.json and metrics.json locally and uploads them
   to s3://ARTIFACTS_BUCKET/ARTIFACTS_PREFIX. metrics.json carries the notebook's validation / test results
   unchanged: the final fit uses every labelled snapshot, so it has no held-out score of its own.
"""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ml.engagement_risk import config, features

FEATURE_DESCRIPTIONS = {
    "txn_count_30d": "eligible transactions in the 30 days before T",
    "spend_usd_30d": "Purchase + Payment amount (USD) in the 30 days before T",
    "txn_count_90d": "eligible transactions in the 90 days before T",
    "spend_usd_90d": "Purchase + Payment amount (USD) in the 90 days before T",
    "txn_count_180d": "eligible transactions in the 180 days before T",
    "spend_usd_180d": "Purchase + Payment amount (USD) in the 180 days before T",
    "active_days_90d": "distinct days with an eligible transaction, 90 days before T",
    "active_days_180d": "distinct days with an eligible transaction, 180 days before T",
    "days_since_last_txn": "calendar days from the last eligible transaction to T",
    "n_txn_types_180d": "distinct eligible transaction types, 180 days before T",
    "n_categories_180d": "distinct spending categories, 180 days before T",
    "count_trend_30d_vs_prior60d": "daily rate in the last 30 days minus the daily rate in the 60 days before",
    "count_ratio_90d_vs_prior90d": "count in the last 90 days / (count in the 90 days before + 1)",
    "txns_per_active_day_180d": "eligible transactions per active day, 180 days before T",
    "declined_count_90d": "declined attempts in the 90 days before T",
    "tenure_days": "calendar days from registration to T",
    "products_opened": "products with an opening date before T",
    "sends_180d": "campaign sends in the 180 days before T",
    "opens_180d": "campaign opens dated in the 180 days before T",
    "clicks_180d": "campaign clicks dated in the 180 days before T",
    "days_since_last_click": f"calendar days from the last campaign click to T (capped at {config.RECENCY_CAP_DAYS})",
    "contacts_180d": "call-centre contacts in the 180 days before T",
    "complaints_180d": "complaints in the 180 days before T",
    "logins_90d": "digital logins in the 90 days before T",
    "digital_active_days_90d": "days with any digital event in the 90 days before T",
    "days_since_last_login": f"calendar days from the last login to T (capped at {config.RECENCY_CAP_DAYS})",
}
assert list(FEATURE_DESCRIPTIONS) == config.FEATURES

REPLICATION_TOLERANCE = 1.5e-4  # the notebook printed metrics rounded to 4 decimals


def fit(rows: pd.DataFrame):
    import lightgbm as lgb

    return lgb.LGBMClassifier(**config.LGBM_PARAMS).fit(features.model_matrix(rows), rows[config.TARGET_NAME].values)


def replication_check(rows: pd.DataFrame) -> dict:
    """Same rows and same validation result as the notebook, or stop."""
    from sklearn.metrics import average_precision_score, roc_auc_score

    reported = config.reported_metrics()
    counts = {str(k): int(v) for k, v in rows.groupby("snapshot_date").size().items()}
    if counts != reported["population_rows"]:
        raise SystemExit(f"population differs from the notebook: {counts} vs {reported['population_rows']}")
    train = rows[rows.snapshot_date.isin(config.NOTEBOOK_SPLITS["train"])]
    valid = rows[rows.snapshot_date.isin(config.NOTEBOOK_SPLITS["validation"])]
    score = fit(train).predict_proba(features.model_matrix(valid))[:, 1]
    got = {"pr_auc": average_precision_score(valid[config.TARGET_NAME], score),
           "roc_auc": roc_auc_score(valid[config.TARGET_NAME], score)}
    want = reported["validation"]["models"]["lightgbm"]
    for k in got:
        if abs(got[k] - want[k]) > REPLICATION_TOLERANCE:
            raise SystemExit(f"validation {k} {got[k]:.5f} does not reproduce the notebook's {want[k]}")
    return {"population_rows_match": True, "validation_pr_auc": round(got["pr_auc"], 6),
            "validation_roc_auc": round(got["roc_auc"], 6), "notebook_pr_auc": want["pr_auc"],
            "notebook_roc_auc": want["roc_auc"], "tolerance": REPLICATION_TOLERANCE}


def main(argv=None) -> None:
    import duckdb
    import lightgbm as lgb
    import sklearn

    from ml.engagement_risk import io

    p = argparse.ArgumentParser(description="Train the frozen engagement-risk model and save its artifacts.")
    p.add_argument("--profile", default=config.DEFAULT_PROFILE, help="AWS profile; '' uses the default chain.")
    p.add_argument("--cache", type=Path, default=Path("data/processed/engagement_risk_cache.duckdb"))
    p.add_argument("--refresh-cache", action="store_true")
    p.add_argument("--output-dir", type=Path, default=Path(f"data/processed/engagement_risk/{config.ARTIFACT_VERSION}"))
    p.add_argument("--skip-replication-check", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="Save artifacts locally only; upload nothing.")
    p.add_argument("--overwrite", action="store_true", help="Replace a different model.txt already in S3.")
    args = p.parse_args(argv)

    session = io.aws_session(args.profile or None)
    con = io.connect(args.profile or None, args.cache)
    source_rows = io.load_sources(con, session.client("glue"), args.refresh_cache)
    rows = features.build_feature_rows(con, config.FINAL_TRAINING_SNAPSHOTS, with_labels=True)
    replication = None if args.skip_replication_check else replication_check(rows)

    model = fit(rows)
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(out / "model.txt"))
    reloaded = lgb.Booster(model_file=str(out / "model.txt"))
    X = features.model_matrix(rows)
    if not np.allclose(reloaded.predict(X), model.predict_proba(X)[:, 1]):
        raise SystemExit("model.txt does not reproduce the fitted model's predictions")

    per_snapshot = rows.groupby("snapshot_date").agg(rows=(config.TARGET_NAME, "size"), prevalence=(config.TARGET_NAME, "mean"))
    snapshots = [{"cutoff_exclusive": str(T), "as_of_date": str(T - timedelta(days=1)),
                  "notebook_split": next(s for s, d in config.NOTEBOOK_SPLITS.items() if T in d),
                  "rows": int(r.rows), "prevalence": round(float(r.prevalence), 4)} for T, r in per_snapshot.iterrows()]
    metadata = {
        "model_name": config.MODEL_NAME, "model_version": config.MODEL_VERSION, "artifact_version": config.ARTIFACT_VERSION,
        "framework": "lightgbm", "model_file": "model.txt (LightGBM native text format)",
        "target_name": config.TARGET_NAME, "target_definition": config.TARGET_DEFINITION,
        "population_definition": config.POPULATION_DEFINITION, "horizon_days": config.HORIZON_DAYS,
        "eligible_transaction_types": list(config.ELIGIBLE_TYPES),
        "snapshot_semantics": "cutoff T is exclusive (midnight); features use data dated before T; labels [T, T+90d); as_of_date = T - 1 day",
        "serving_as_of_date": str(config.SERVING_AS_OF), "serving_cutoff_exclusive": str(config.cutoff_for(config.SERVING_AS_OF)),
        "training_snapshots": snapshots, "training_rows": int(len(rows)),
        "training_prevalence": round(float(rows[config.TARGET_NAME].mean()), 4),
        "final_hyperparameters": config.LGBM_PARAMS,
        "hyperparameter_provenance": "selected on validation PR-AUC in the notebook (section 11); n_estimators = best iteration (77); not re-tuned",
        "feature_count": len(config.FEATURES), "feature_schema": "feature_schema.json",
        "independent_benchmark": "metrics.json -> reported.test (2026-01-01, evaluated once before this final fit)",
        "source_tables": {name: f"{config.SILVER_DB}.{spec[0]}" for name, spec in io.SOURCES.items()}
                         | {"src_digital_daily": f"{config.SILVER_DB}.digital_events (daily counts)"},
        "source_rows": source_rows, "notebook": "notebooks/engagement_risk_feasibility_v1.ipynb",
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "library_versions": {"lightgbm": lgb.__version__, "scikit-learn": sklearn.__version__, "duckdb": duckdb.__version__,
                             "pandas": pd.__version__, "numpy": np.__version__},
    }
    schema = {"model_version": config.MODEL_VERSION, "order_matters": True, "dtype": "float64",
              "features": [{"position": i, "name": f, "description": FEATURE_DESCRIPTIONS[f]} for i, f in enumerate(config.FEATURES)],
              "missing_values": "none: counts default to 0 and unknown click / login recency to "
                                f"{config.RECENCY_CAP_DAYS}; a row with a non-finite input is not scored (insufficient_data)"}
    metrics = {"model_version": config.MODEL_VERSION, "reported": config.reported_metrics(), "replication_check": replication,
               "final_fit": {"snapshots": [s["cutoff_exclusive"] for s in snapshots], "rows": int(len(rows)),
                             "note": "every labelled snapshot is in the final fit, so it has no held-out metric; "
                                     "the reported test result is the independent benchmark"}}
    for name, doc in (("metadata.json", metadata), ("feature_schema.json", schema), ("metrics.json", metrics)):
        (out / name).write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")

    uploaded = [] if args.dry_run else io.upload_artifacts(session.client("s3"), out, args.overwrite)
    print(json.dumps({"training_snapshots": snapshots, "training_rows": len(rows), "replication_check": replication,
                      "local_artifacts": str(out), "uploaded": uploaded}, indent=2, default=str))


if __name__ == "__main__":
    main()
