"""Engagement-risk pipeline: features, eligibility, fallbacks, tiers, output schema, determinism. No AWS."""

from datetime import date, datetime, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest

from ml.engagement_risk import config, features, io, score, train

AS_OF = date(2026, 6, 17)
T = config.cutoff_for(AS_OF)  # 2026-06-18, exclusive
D = lambda days: datetime(2026, 6, 18) - timedelta(days=days)  # noqa: E731 - a timestamp `days` before the cutoff


def txn(cid, ts, ttype="Purchase", status="Approved", category="Food", amount=10.0, reasons=()):
    return {"transaction_id": f"{cid}-{ts:%Y%m%d%H%M}-{ttype}", "customer_id": cid, "transaction_date": ts,
            "business_date": ts.date(), "transaction_type": ttype, "transaction_category": category,
            "merchant_category": None, "transaction_status": status, "amount": amount, "currency": "USD",
            "amount_usd": amount, "dq_reasons": list(reasons)}


CUSTOMERS = [
    ("C_ACTIVE", datetime(2020, 1, 1)),       # established, active: model
    ("C_LAPSED", datetime(2020, 1, 1)),       # established, last eligible txn > 365 days ago: no_eligible_txn_365d
    ("C_NEW_QUIET", D(30)),                   # 30 days old, no transactions: new customer
    ("C_NEW_ACTIVE", D(30)),                  # 30 days old with a transaction: model (same population rule as training)
    ("C_NO_REG", None),                       # no registration date: insufficient_data
    ("C_FUTURE", datetime(2026, 6, 20)),      # registers after the as-of date: not a customer yet
    ("C_ADJ_ONLY", datetime(2021, 5, 1)),     # only adjustments / declined attempts: no eligible activity
    ("C_CENTS", datetime(2022, 2, 1)),        # 0.1 + 0.2 USD: spend must be summed exactly
]
TRANSACTIONS = [
    txn("C_ACTIVE", D(2)), txn("C_ACTIVE", D(10), "Transfer"), txn("C_ACTIVE", D(40), "Deposit"),
    txn("C_ACTIVE", D(100), "Withdrawal"), txn("C_ACTIVE", D(20), status="Declined"),
    txn("C_ACTIVE", D(15), "Adjustment"),
    txn("C_ACTIVE", datetime(2026, 6, 18, 3, 0)),            # at/after the cutoff: must not reach features
    txn("C_ACTIVE", D(5), reasons=["orphan_products"]),     # excluded by gold rules
    txn("C_LAPSED", D(400)),
    txn("C_NEW_ACTIVE", D(5), "Payment"),
    txn("C_ADJ_ONLY", D(30), "Adjustment"), txn("C_ADJ_ONLY", D(31), status="Declined"),
    txn("C_CENTS", D(3), amount=0.1), txn("C_CENTS", D(4), amount=0.2),
]


@pytest.fixture
def con():
    c = duckdb.connect()
    c.register("_t", pd.DataFrame(TRANSACTIONS))
    c.sql("""CREATE TABLE src_transactions AS SELECT transaction_id, customer_id, transaction_date::TIMESTAMP AS transaction_date,
             business_date::DATE AS business_date, transaction_type, transaction_category, merchant_category::VARCHAR AS merchant_category,
             transaction_status, amount::DOUBLE AS amount, currency, amount_usd::DOUBLE AS amount_usd,
             dq_reasons::VARCHAR[] AS dq_reasons FROM _t""")
    c.register("_c", pd.DataFrame(CUSTOMERS, columns=["customer_id", "registration_date"]))
    c.sql("CREATE TABLE src_customers AS SELECT customer_id, registration_date::TIMESTAMP AS registration_date FROM _c")
    c.sql("CREATE TABLE src_products (customer_id VARCHAR, opening_date DATE)")
    c.sql("INSERT INTO src_products VALUES ('C_ACTIVE', DATE '2021-01-01'), ('C_ACTIVE', DATE '2026-06-30')")  # second: after T
    c.sql("""CREATE TABLE src_sends (customer_id VARCHAR, campaign_id VARCHAR, send_date TIMESTAMP, send_channel VARCHAR,
             was_delivered BOOLEAN, was_opened BOOLEAN, open_date TIMESTAMP, was_clicked BOOLEAN, click_date TIMESTAMP)""")
    c.sql(f"INSERT INTO src_sends VALUES ('C_ACTIVE', 'K1', TIMESTAMP '{D(50)}', 'Email', true, true, TIMESTAMP '{D(49)}', true, TIMESTAMP '{D(48)}')")
    c.sql("CREATE TABLE src_contacts (customer_id VARCHAR, interaction_date TIMESTAMP)")
    c.sql("CREATE TABLE src_complaints (customer_id VARCHAR, creation_date TIMESTAMP)")
    c.sql("CREATE TABLE src_digital_daily (customer_id VARCHAR, day DATE, events BIGINT, logins BIGINT)")
    c.sql(f"INSERT INTO src_digital_daily VALUES ('C_ACTIVE', DATE '{D(3).date()}', 4, 1)")
    yield c
    c.close()


def customers_frame(con):
    return con.sql("SELECT customer_id, registration_date FROM src_customers").df()


def fake_predict(X):
    return np.clip(X.txn_count_180d.to_numpy() / 10, 0, 1)


# ---- features ----


def test_feature_schema_is_the_frozen_list_in_order(con):
    rows = features.build_feature_rows(con, [T], with_labels=False)
    assert len(config.FEATURES) == 26 and len(set(config.FEATURES)) == 26
    assert [c for c in rows.columns if c in config.FEATURES] == config.FEATURES
    assert list(features.model_matrix(rows).columns) == config.FEATURES
    assert list(train.FEATURE_DESCRIPTIONS) == config.FEATURES


def test_population_is_registered_before_T_with_eligible_activity_in_365d(con):
    rows = features.build_feature_rows(con, [T], with_labels=False)
    assert set(rows.customer_id) == {"C_ACTIVE", "C_NEW_ACTIVE", "C_CENTS"}


def test_features_ignore_data_at_or_after_the_cutoff_and_excluded_rows(con):
    r = features.build_feature_rows(con, [T], with_labels=False).set_index("customer_id").loc["C_ACTIVE"]
    assert r.txn_count_30d == 2      # D(2), D(10); not the 03:00 one after the cutoff, nor the excluded row
    assert r.txn_count_90d == 3 and r.txn_count_180d == 4
    assert r.days_since_last_txn == 2 and r.declined_count_90d == 1
    assert r.products_opened == 1    # the product opened after T is not counted
    assert r.clicks_180d == 1 and r.days_since_last_click == 48 and r.logins_90d == 1
    assert pd.Timestamp(r.last_ts_used) < pd.Timestamp(T)


def test_labels_come_only_from_the_future_window(con):
    # Cutoff 2026-04-01: D(100) = 2026-03-10 is history; D(40), D(10), D(2) and 2026-06-18 03:00 fall in the 90-day
    # label window (the gold-excluded D(5) row does not count).
    earlier = date(2026, 4, 1)
    r = features.build_feature_rows(con, [earlier], with_labels=True).set_index("customer_id").loc["C_ACTIVE"]
    assert r.txn_count_180d == 1 and r.fut_txn_count == 4 and r[config.TARGET_NAME] == 0
    assert pd.Timestamp(r.last_ts_used) < pd.Timestamp(earlier)


def test_spend_is_summed_exactly_so_reruns_train_identical_models(con):
    # A DOUBLE sum gives 0.30000000000000004 and depends on row order; the DECIMAL sum is exact.
    r = features.build_feature_rows(con, [T], with_labels=False).set_index("customer_id").loc["C_CENTS"]
    assert r.spend_usd_30d == 0.3 and r.spend_usd_180d == 0.3


def test_a_row_using_future_data_is_refused():
    bad = pd.DataFrame({**{f: [0.0] for f in config.FEATURES}, "snapshot_date": [T], "last_ts_used": [pd.Timestamp(T)]})
    with pytest.raises(ValueError, match="at or after its cutoff"):
        features.check_feature_rows(bad)


# ---- eligibility and fallbacks ----


@pytest.fixture
def scored(con):
    rows = features.build_feature_rows(con, [T], with_labels=False)
    out, thresholds = score.build_scores(customers_frame(con), rows, AS_OF, fake_predict)
    return out.set_index("customer_id"), thresholds


def test_eligible_customers_are_model_scored(scored):
    out, _ = scored
    model = out[out.model_eligible]
    assert set(model.index) == {"C_ACTIVE", "C_NEW_ACTIVE", "C_CENTS"}
    assert (model.scoring_source == "model").all() and model.risk_score.notna().all() and model.reason_code.isna().all()


def test_established_customer_without_eligible_activity_is_a_high_tier_fallback(scored):
    out, _ = scored
    for cid in ("C_LAPSED", "C_ADJ_ONLY"):  # adjustments and declined attempts are not eligible activity
        r = out.loc[cid]
        assert (r.model_eligible, r.scoring_source, r.risk_tier, r.reason_code) == (False, "fallback", "high", "no_eligible_txn_365d")
        assert pd.isna(r.risk_score)


def test_new_customer_without_history_is_not_called_high_risk(scored):
    r = scored[0].loc["C_NEW_QUIET"]
    assert (r.model_eligible, r.risk_tier, r.reason_code) == (False, "new_customer", "insufficient_history_new_customer")
    assert pd.isna(r.risk_score)


def test_missing_registration_is_insufficient_data_and_future_registrations_are_left_out(scored):
    out, _ = scored
    r = out.loc["C_NO_REG"]
    assert (r.risk_tier, r.reason_code, r.scoring_source) == ("unknown", "insufficient_data", "fallback") and pd.isna(r.risk_score)
    assert "C_FUTURE" not in out.index


def test_a_non_finite_model_input_falls_back_to_insufficient_data(con):
    rows = features.build_feature_rows(con, [T], with_labels=False)
    rows.loc[rows.customer_id == "C_NEW_ACTIVE", "spend_usd_90d"] = np.nan
    out, _ = score.build_scores(customers_frame(con), rows, AS_OF, fake_predict)
    r = out.set_index("customer_id").loc["C_NEW_ACTIVE"]
    assert (r.model_eligible, r.reason_code, r.risk_tier) == (False, "insufficient_data", "unknown") and pd.isna(r.risk_score)


@pytest.mark.parametrize(("days", "reason"), [(89, "insufficient_history_new_customer"), (90, "no_eligible_txn_365d")])
def test_new_customer_tenure_cutoff(days, reason):
    customers = pd.DataFrame({"customer_id": ["X"], "registration_date": [datetime(2026, 6, 18) - timedelta(days=days)]})
    empty = pd.DataFrame(columns=["customer_id", *config.FEATURES])
    out, _ = score.build_scores(customers, empty, AS_OF, fake_predict)
    assert out.reason_code.iloc[0] == reason


# ---- tiers ----


def test_tiers_are_top_20_next_30_rest_with_deterministic_ties():
    ids = pd.Series([f"C{i:02d}" for i in range(10)])
    scores = np.array([0.9, 0.8, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1])
    tiers, thresholds = score.assign_tiers(ids, scores)
    assert list(tiers) == ["high", "high", "medium", "medium", "medium", "low", "low", "low", "low", "low"]
    assert thresholds == {"high_min_score": 0.8, "medium_min_score": 0.6}
    shuffled = np.random.default_rng(0).permutation(10)
    again, _ = score.assign_tiers(ids.iloc[shuffled].reset_index(drop=True), scores[shuffled])
    assert dict(zip(ids.iloc[shuffled], again)) == dict(zip(ids, tiers))


def test_tier_shares_on_a_larger_population():
    ids = pd.Series([f"C{i:04d}" for i in range(1000)])
    tiers, _ = score.assign_tiers(ids, np.random.default_rng(1).random(1000))
    assert tiers.value_counts().to_dict() == {"low": 500, "medium": 300, "high": 200}


# ---- output schema ----


def test_output_schema_and_invariants(scored, tmp_path):
    out, _ = scored
    frame = out.reset_index()[score.OUTPUT_COLUMNS]
    assert list(frame.columns) == score.OUTPUT_COLUMNS == [
        "customer_id", "as_of_date", "model_version", "model_eligible", "scoring_source", "risk_score", "risk_tier", "reason_code"]
    score.validate_scores(frame)
    assert (frame.model_version == config.MODEL_VERSION).all() and (frame.as_of_date == AS_OF).all()
    assert not set(config.FEATURES) & set(frame.columns)  # no raw model features in the serving output
    path = tmp_path / "part-00000.parquet"
    io.write_parquet(frame, path)
    back = duckdb.sql(f"SELECT * FROM '{path.as_posix()}' ORDER BY customer_id").df()
    assert [c for c, _ in io.GOLD_COLUMNS] == list(back.columns)
    assert back.loc[~back.model_eligible, "risk_score"].isna().all() and back.loc[back.model_eligible, "risk_score"].notna().all()


@pytest.mark.parametrize("breakage", ["duplicate", "fallback_score", "missing_model_score"])
def test_validation_catches_broken_outputs(scored, breakage):
    frame = scored[0].reset_index()[score.OUTPUT_COLUMNS].copy()
    if breakage == "duplicate":
        frame = pd.concat([frame, frame.iloc[[0]]])
    elif breakage == "fallback_score":
        frame.loc[~frame.model_eligible, "risk_score"] = 0.5
    else:
        frame.loc[frame.model_eligible, "risk_score"] = np.nan
    with pytest.raises(ValueError):
        score.validate_scores(frame)


# ---- determinism ----


def test_lightgbm_with_the_frozen_parameters_is_deterministic_and_round_trips(tmp_path):
    import lightgbm as lgb

    rng = np.random.default_rng(3)
    X = pd.DataFrame(rng.normal(size=(600, len(config.FEATURES))), columns=config.FEATURES)
    y = (X.txn_count_180d + rng.normal(size=600) < 0).astype(int)
    params = {**config.LGBM_PARAMS, "n_estimators": 20}
    a = lgb.LGBMClassifier(**params).fit(X, y)
    b = lgb.LGBMClassifier(**params).fit(X, y)
    assert np.array_equal(a.predict_proba(X)[:, 1], b.predict_proba(X)[:, 1])
    a.booster_.save_model(str(tmp_path / "model.txt"))
    b.booster_.save_model(str(tmp_path / "again.txt"))
    assert (tmp_path / "model.txt").read_bytes() == (tmp_path / "again.txt").read_bytes()
    reloaded = lgb.Booster(model_file=str(tmp_path / "model.txt"))
    assert np.allclose(reloaded.predict(X), a.predict_proba(X)[:, 1])


def test_scoring_twice_gives_identical_rows(con):
    rows = features.build_feature_rows(con, [T], with_labels=False)
    first, _ = score.build_scores(customers_frame(con), rows, AS_OF, fake_predict)
    second, _ = score.build_scores(customers_frame(con), rows.sample(frac=1, random_state=5), AS_OF, fake_predict)
    pd.testing.assert_frame_equal(first, second)


def test_frozen_configuration_matches_the_notebook_selection():
    p = config.LGBM_PARAMS
    assert (p["learning_rate"], p["num_leaves"], p["max_depth"], p["min_child_samples"], p["n_estimators"]) == (0.05, 127, 6, 50, 77)
    assert (p["subsample"], p["colsample_bytree"], p["reg_alpha"], p["reg_lambda"], p["random_state"]) == (1.0, 0.8, 0.1, 1.0, 7)
    assert config.FINAL_TRAINING_SNAPSHOTS == [date(2024, 7, 1), date(2024, 10, 1), date(2025, 1, 1), date(2025, 4, 1),
                                               date(2025, 7, 1), date(2025, 10, 1), date(2026, 1, 1)]
    last = config.FINAL_TRAINING_SNAPSHOTS[-1]
    assert last + timedelta(days=config.HORIZON_DAYS) <= config.SERVING_AS_OF  # every final-fit label closed before serving
    assert config.reported_metrics()["test"]["models"]["lightgbm"]["pr_auc"] == 0.6563
