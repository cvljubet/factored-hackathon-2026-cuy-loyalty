"""Batch scoring: one serving row per customer as of a date, written to Gold.

    python -m ml.engagement_risk.score --profile cuy-loyalty --as-of 2026-06-17

Every customer gets a row. Customers in the training population (registered before T, at least one eligible
transaction in the 365 days before T) get the LightGBM probability. Everyone else gets a deterministic
fallback with risk_score = NULL: no probability is invented for them.

| Case | risk_tier | reason_code |
|---|---|---|
| model-scored | high (top 20 %) / medium (next 30 %) / low | NULL |
| no eligible transaction in 365 days, tenure >= 90 days | high | no_eligible_txn_365d |
| no eligible transaction in 365 days, tenure < 90 days | new_customer | insufficient_history_new_customer |
| missing registration date, or a model input not finite | unknown | insufficient_data |
"""

import argparse
import json
import math
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ml.engagement_risk import config, features

OUTPUT_COLUMNS = ["customer_id", "as_of_date", "model_version", "model_eligible", "scoring_source", "risk_score",
                  "risk_tier", "reason_code"]
REASON_NO_TXN = "no_eligible_txn_365d"
REASON_NEW = "insufficient_history_new_customer"
REASON_DATA = "insufficient_data"
MODEL_TIERS = ("high", "medium", "low")
FALLBACK_TIERS = {REASON_NO_TXN: "high", REASON_NEW: "new_customer", REASON_DATA: "unknown"}


def assign_tiers(customer_ids: pd.Series, scores: np.ndarray) -> tuple[pd.Series, dict[str, float]]:
    """high = top TIER_HIGH_SHARE of scores, medium = the next TIER_MEDIUM_SHARE, low = the rest. Ranked by score
    descending, then customer_id, so ties and reruns always give the same tiers."""
    order = pd.DataFrame({"customer_id": customer_ids.values, "score": scores}).sort_values(
        ["score", "customer_id"], ascending=[False, True], kind="stable")
    n = len(order)
    k_high = math.ceil(config.TIER_HIGH_SHARE * n)
    k_medium = math.ceil((config.TIER_HIGH_SHARE + config.TIER_MEDIUM_SHARE) * n)
    rank = np.arange(n)
    order["tier"] = np.where(rank < k_high, "high", np.where(rank < k_medium, "medium", "low"))
    thresholds = {
        "high_min_score": float(order.score.iloc[k_high - 1]) if k_high else math.nan,
        "medium_min_score": float(order.score.iloc[k_medium - 1]) if k_medium else math.nan,
    }
    return order.set_index("customer_id").tier.reindex(customer_ids.values).set_axis(customer_ids.index), thresholds


def build_scores(customers: pd.DataFrame, feature_rows: pd.DataFrame, as_of: date,
                 predict: Callable[[pd.DataFrame], np.ndarray]) -> tuple[pd.DataFrame, dict]:
    """The serving rows for every customer registered by the cutoff, and the tier thresholds.

    customers: customer_id, registration_date. feature_rows: features.build_feature_rows for this cutoff.
    """
    cutoff = config.cutoff_for(as_of)
    if customers.customer_id.isna().any() or customers.customer_id.duplicated().any():
        raise ValueError("customers must have unique, non-null customer_id")
    reg = pd.to_datetime(customers.registration_date)
    universe = customers[reg.isna() | (reg < pd.Timestamp(cutoff))].copy()  # not yet registered: not a customer yet
    universe["registration_date"] = pd.to_datetime(universe.registration_date)

    rows = feature_rows.set_index("customer_id")
    in_population = universe.customer_id.isin(rows.index)
    out = pd.DataFrame({"customer_id": universe.customer_id.values, "as_of_date": as_of,
                        "model_version": config.MODEL_VERSION}, index=universe.index)
    out["model_eligible"] = False
    out["risk_score"] = np.nan
    out["risk_tier"] = None
    out["reason_code"] = None

    # Model population, unless a model input is unusable.
    pop_ids = universe.customer_id[in_population]
    X = features.model_matrix(rows.loc[pop_ids.values])
    usable = np.isfinite(X.to_numpy()).all(axis=1)
    model_idx = pop_ids.index[usable]
    out.loc[pop_ids.index[~usable], "reason_code"] = REASON_DATA
    if len(model_idx):
        scores = np.asarray(predict(X[usable]), dtype=float)
        out.loc[model_idx, "risk_score"] = scores
        out.loc[model_idx, "model_eligible"] = True
        tiers, thresholds = assign_tiers(out.loc[model_idx, "customer_id"], scores)
        out.loc[model_idx, "risk_tier"] = tiers
    else:
        thresholds = {"high_min_score": math.nan, "medium_min_score": math.nan}

    # Outside the population: missing registration, new customer, or established but without eligible activity.
    rest = universe.index[~in_population]
    no_registration = universe.registration_date.isna()
    tenure = (pd.Timestamp(cutoff) - universe.registration_date.dt.normalize()).dt.days
    missing = no_registration.loc[rest].to_numpy()
    out.loc[rest[missing], "reason_code"] = REASON_DATA
    known = rest[~missing]
    new = (tenure.loc[known] < config.NEW_CUSTOMER_TENURE_DAYS).to_numpy()
    out.loc[known[new], "reason_code"] = REASON_NEW
    out.loc[known[~new], "reason_code"] = REASON_NO_TXN

    fallback = ~out.model_eligible
    out.loc[fallback, "risk_tier"] = out.loc[fallback, "reason_code"].map(FALLBACK_TIERS)
    out["scoring_source"] = np.where(out.model_eligible, "model", "fallback")
    out["risk_score"] = out.risk_score.where(out.model_eligible)
    return out[OUTPUT_COLUMNS].sort_values("customer_id").reset_index(drop=True), thresholds


def validate_scores(scores: pd.DataFrame) -> None:
    """The serving invariants; raises on the first broken one."""
    if list(scores.columns) != OUTPUT_COLUMNS:
        raise ValueError(f"columns {list(scores.columns)} != {OUTPUT_COLUMNS}")
    if scores.customer_id.duplicated().any():
        raise ValueError("duplicate customer_id")
    model = scores[scores.model_eligible]
    if model.risk_score.isna().any() or not model.risk_score.between(0, 1).all():
        raise ValueError("model-scored rows need a probability in [0, 1]")
    if not set(model.risk_tier) <= set(MODEL_TIERS) or model.reason_code.notna().any():
        raise ValueError("model rows need a model tier and no reason_code")
    fb = scores[~scores.model_eligible]
    if fb.risk_score.notna().any():
        raise ValueError("fallback rows must have risk_score = NULL")
    if not (fb.reason_code.map(FALLBACK_TIERS) == fb.risk_tier).all():
        raise ValueError("fallback tier does not match its reason_code")
    if not ((scores.scoring_source == "model") == scores.model_eligible).all():
        raise ValueError("scoring_source disagrees with model_eligible")


def summarize(scores: pd.DataFrame, thresholds: dict) -> dict:
    model = scores[scores.model_eligible]
    n = len(scores)
    return {
        "as_of_date": str(scores.as_of_date.iloc[0]), "model_version": config.MODEL_VERSION, "customers": n,
        "model_eligible": int(len(model)), "model_eligible_pct": round(len(model) / n, 4),
        "fallback_by_reason": scores[~scores.model_eligible].reason_code.value_counts().to_dict(),
        "risk_tier_counts": scores.risk_tier.value_counts().to_dict(),
        "model_tier_counts": model.risk_tier.value_counts().to_dict(),
        "model_score_quantiles": {str(q): round(float(model.risk_score.quantile(q)), 4)
                                  for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.8, 0.9, 1.0)} if len(model) else {},
        "model_score_mean": round(float(model.risk_score.mean()), 4) if len(model) else None,
        "tier_thresholds": {k: round(v, 6) for k, v in thresholds.items()},
        "duplicate_customer_ids": int(scores.customer_id.duplicated().sum()),
        "model_rows_with_null_score": int(model.risk_score.isna().sum()),
        "fallback_rows_with_score": int(scores[~scores.model_eligible].risk_score.notna().sum()),
    }


def main(argv=None) -> None:
    import lightgbm as lgb

    from ml.engagement_risk import io

    p = argparse.ArgumentParser(description="Score every customer for engagement risk and write the Gold table.")
    p.add_argument("--profile", default=config.DEFAULT_PROFILE, help="AWS profile; '' uses the default chain.")
    p.add_argument("--as-of", type=date.fromisoformat, default=config.SERVING_AS_OF)
    p.add_argument("--cache", type=Path, default=Path("data/processed/engagement_risk_cache.duckdb"))
    p.add_argument("--refresh-cache", action="store_true")
    p.add_argument("--model-dir", type=Path, help="Local artifacts instead of downloading them from S3.")
    p.add_argument("--output-dir", type=Path, default=Path("data/processed/engagement_risk/scores"))
    p.add_argument("--dry-run", action="store_true", help="Score and validate locally; write nothing to S3 or Glue.")
    args = p.parse_args(argv)

    session = io.aws_session(args.profile or None)
    s3 = session.client("s3")
    model_dir = args.model_dir or io.download_artifacts(s3, args.output_dir / "model")
    schema = json.loads((model_dir / "feature_schema.json").read_text(encoding="utf-8"))
    metadata = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
    if [f["name"] for f in schema["features"]] != config.FEATURES or metadata["model_version"] != config.MODEL_VERSION:
        raise SystemExit("model artifacts do not match this code's feature schema / model version")
    booster = lgb.Booster(model_file=str(model_dir / "model.txt"))

    con = io.connect(args.profile or None, args.cache)
    io.load_sources(con, session.client("glue"), args.refresh_cache)
    features.prepare_activity(con)
    last_day = con.sql("SELECT CAST(max(ts) AS DATE) FROM activity").fetchone()[0]
    if args.as_of > last_day:
        raise SystemExit(f"as-of {args.as_of} is after the last data day {last_day}")
    cutoff = config.cutoff_for(args.as_of)
    rows = features.build_feature_rows(con, [cutoff], with_labels=False)
    customers = con.sql("SELECT customer_id, registration_date FROM src_customers").df()

    scores, thresholds = build_scores(customers, rows, args.as_of, lambda X: booster.predict(X))
    validate_scores(scores)
    summary = summarize(scores, thresholds) | {
        "cutoff_exclusive": str(cutoff), "feature_rows": len(rows), "scored_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "new_customer_tenure_days": config.NEW_CUSTOMER_TENURE_DAYS,
        "tier_rule": f"high = top {config.TIER_HIGH_SHARE:.0%} of model scores, medium = next {config.TIER_MEDIUM_SHARE:.0%}, low = rest",
    }
    out_dir = args.output_dir / f"as_of_date={args.as_of}"
    io.write_parquet(scores, out_dir / "part-00000.parquet")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if not args.dry_run:
        summary["gold"] = io.publish_gold(session, out_dir / "part-00000.parquet", str(args.as_of))
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        key = f"{config.ARTIFACTS_PREFIX}scoring/as_of_date={args.as_of}/summary.json"
        s3.upload_file(str(out_dir / "summary.json"), config.ARTIFACTS_BUCKET, key)
        summary["summary_uri"] = f"s3://{config.ARTIFACTS_BUCKET}/{key}"
    examples = scores.groupby("risk_tier", group_keys=False).head(2).assign(customer_id=lambda d: d.customer_id.map(io.pseudonym))
    print(json.dumps(summary, indent=2, default=str))
    print("\nexample rows (customer_id pseudonymised):")
    print(examples.to_string(index=False))


if __name__ == "__main__":
    main()
