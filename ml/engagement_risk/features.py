"""Leakage-safe customer features and labels, ported unchanged from the notebook's build_dataset().

Works on DuckDB source tables (loaded by io.load_sources, or built in tests):

    src_transactions   transaction_id, customer_id, transaction_date, transaction_type, transaction_category,
                       merchant_category, transaction_status, amount, currency, amount_usd, dq_reasons
    src_customers      customer_id, registration_date
    src_products       customer_id, opening_date                       (gold exclusions already applied)
    src_sends          customer_id, send_date, was_delivered, open_date, click_date, ...  (exclusions applied)
    src_contacts       customer_id, interaction_date                   (exclusions applied)
    src_complaints     customer_id, creation_date                      (exclusions applied)
    src_digital_daily  customer_id, day, events, logins

Every feature uses only rows dated before the snapshot cutoff T; labels use [T, T + 90d) only.
"""

from collections.abc import Sequence
from datetime import date

import duckdb
import pandas as pd

from ml.engagement_risk import config


def prepare_activity(con: duckdb.DuckDBPyConnection) -> None:
    """Views of eligible activity and declined attempts (notebook section 2)."""
    types = ", ".join(f"'{t}'" for t in config.ELIGIBLE_TYPES)
    exclude = ", ".join(f"'{r}'" for r in config.EXCLUDE["transactions"])
    con.sql(f"""CREATE OR REPLACE VIEW activity AS
        SELECT t.customer_id, t.transaction_date AS ts, t.transaction_type,
               coalesce(t.transaction_category, t.merchant_category) AS category,
               coalesce(t.amount_usd, CASE WHEN t.currency = 'USD' THEN t.amount END)::DOUBLE AS amount_usd
        FROM src_transactions t
        WHERE t.transaction_status = 'Approved' AND t.transaction_type IN ({types}) AND t.customer_id IS NOT NULL
          AND NOT list_has_any(t.dq_reasons, [{exclude}])""")
    con.sql(f"""CREATE OR REPLACE VIEW declined AS
        SELECT customer_id, transaction_date AS ts FROM src_transactions
        WHERE transaction_status = 'Declined' AND customer_id IS NOT NULL AND NOT list_has_any(dq_reasons, [{exclude}])""")


def build_feature_rows(con: duckdb.DuckDBPyConnection, cutoffs: Sequence[date], with_labels: bool) -> pd.DataFrame:
    """One row per eligible customer x cutoff T: config.FEATURES from data before T, plus last_ts_used (audit)
    and, with_labels, fut_txn_count and the target from [T, T + HORIZON_DAYS)."""
    prepare_activity(con)
    snaps = ", ".join(f"TIMESTAMP '{T:%Y-%m-%d}'" for T in cutoffs)
    I = lambda d: f"INTERVAL {d} DAY"  # noqa: E731
    lookback = config.POPULATION_LOOKBACK_DAYS
    cap = config.RECENCY_CAP_DAYS
    # Spend is summed as DECIMAL (exact) and returned as DOUBLE: a parallel DOUBLE sum depends on row order in its
    # last bits, which made two runs train different trees. Amounts are kept to 1e-6 USD.
    tx_windows = ",\n".join(
        f"count(*) FILTER (WHERE a.ts >= s.T - {I(d)}) AS txn_count_{d}d, "
        f"coalesce(sum(CAST(a.amount_usd AS DECIMAL(18, 6))) FILTER (WHERE a.ts >= s.T - {I(d)} "
        f"AND a.transaction_type IN ('Purchase', 'Payment')), 0)::DOUBLE AS spend_usd_{d}d"
        for d in config.WINDOWS)
    fut_cte = (f""",
    fut AS (SELECT a.customer_id, s.T, count(*) AS fut_txn_count FROM activity a
            JOIN s ON a.ts >= s.T AND a.ts < s.T + {I(config.HORIZON_DAYS)} GROUP BY a.customer_id, s.T)""" if with_labels else "")
    fut_cols = ", coalesce(fut.fut_txn_count, 0) AS fut_txn_count" if with_labels else ""
    fut_join = "LEFT JOIN fut ON fut.customer_id = pop.customer_id AND fut.T = pop.T" if with_labels else ""
    df = con.sql(f"""
    WITH s AS (SELECT unnest([{snaps}]) AS T),
    pop AS (SELECT DISTINCT a.customer_id, s.T FROM activity a JOIN s ON a.ts >= s.T - {I(lookback)} AND a.ts < s.T
            JOIN src_customers c ON c.customer_id = a.customer_id AND c.registration_date < s.T),
    tx AS (SELECT a.customer_id, s.T, {tx_windows},
                  count(DISTINCT CAST(a.ts AS DATE)) FILTER (WHERE a.ts >= s.T - {I(90)}) AS active_days_90d,
                  count(DISTINCT CAST(a.ts AS DATE)) FILTER (WHERE a.ts >= s.T - {I(180)}) AS active_days_180d,
                  date_diff('day', max(a.ts), s.T) AS days_since_last_txn,
                  count(DISTINCT a.transaction_type) FILTER (WHERE a.ts >= s.T - {I(180)}) AS n_txn_types_180d,
                  count(DISTINCT a.category) FILTER (WHERE a.ts >= s.T - {I(180)}) AS n_categories_180d,
                  count(*) FILTER (WHERE a.ts >= s.T - {I(90)} AND a.ts < s.T - {I(30)}) AS txn_count_prior60d,
                  count(*) FILTER (WHERE a.ts >= s.T - {I(180)} AND a.ts < s.T - {I(90)}) AS txn_count_prior90d,
                  max(a.ts) AS last_ts_used
           FROM activity a JOIN s ON a.ts < s.T AND a.ts >= s.T - {I(lookback)} GROUP BY a.customer_id, s.T),
    dec AS (SELECT d.customer_id, s.T, count(*) AS declined_count_90d FROM declined d JOIN s ON d.ts < s.T AND d.ts >= s.T - {I(90)}
            GROUP BY d.customer_id, s.T),
    prod AS (SELECT p.customer_id, s.T, count(*) AS products_opened FROM src_products p JOIN s ON p.opening_date < CAST(s.T AS DATE)
             GROUP BY p.customer_id, s.T),
    camp AS (SELECT e.customer_id, s.T,
                    count(*) FILTER (WHERE e.send_date >= s.T - {I(180)}) AS sends_180d,
                    count(*) FILTER (WHERE e.open_date < s.T AND e.open_date >= s.T - {I(180)}) AS opens_180d,
                    count(*) FILTER (WHERE e.click_date < s.T AND e.click_date >= s.T - {I(180)}) AS clicks_180d,
                    date_diff('day', max(e.click_date) FILTER (WHERE e.click_date < s.T), s.T) AS days_since_last_click
             FROM src_sends e JOIN s ON e.send_date < s.T AND e.send_date >= s.T - {I(lookback)} GROUP BY e.customer_id, s.T),
    svc AS (SELECT x.customer_id, s.T, count(*) AS contacts_180d FROM src_contacts x JOIN s
            ON x.interaction_date < s.T AND x.interaction_date >= s.T - {I(180)} GROUP BY x.customer_id, s.T),
    cmp AS (SELECT x.customer_id, s.T, count(*) AS complaints_180d FROM src_complaints x JOIN s
            ON x.creation_date < s.T AND x.creation_date >= s.T - {I(180)} GROUP BY x.customer_id, s.T),
    dig AS (SELECT g.customer_id, s.T,
                   coalesce(sum(g.logins) FILTER (WHERE g.day >= CAST(s.T - {I(90)} AS DATE)), 0) AS logins_90d,
                   count(*) FILTER (WHERE g.day >= CAST(s.T - {I(90)} AS DATE)) AS digital_active_days_90d,
                   date_diff('day', max(g.day) FILTER (WHERE g.logins > 0), CAST(s.T AS DATE)) AS days_since_last_login
            FROM src_digital_daily g JOIN s ON g.day < CAST(s.T AS DATE) AND g.day >= CAST(s.T - {I(lookback)} AS DATE)
            GROUP BY g.customer_id, s.T){fut_cte}
    SELECT pop.customer_id, CAST(pop.T AS DATE) AS snapshot_date,
           tx.* EXCLUDE (customer_id, T, txn_count_prior60d, txn_count_prior90d, last_ts_used),
           tx.txn_count_30d / 30.0 - tx.txn_count_prior60d / 60.0 AS count_trend_30d_vs_prior60d,
           tx.txn_count_90d / (tx.txn_count_prior90d + 1.0) AS count_ratio_90d_vs_prior90d,
           tx.txn_count_180d / greatest(tx.active_days_180d, 1) AS txns_per_active_day_180d,
           coalesce(dec.declined_count_90d, 0) AS declined_count_90d,
           date_diff('day', c.registration_date, pop.T) AS tenure_days,
           coalesce(prod.products_opened, 0) AS products_opened,
           coalesce(camp.sends_180d, 0) AS sends_180d, coalesce(camp.opens_180d, 0) AS opens_180d,
           coalesce(camp.clicks_180d, 0) AS clicks_180d, least(coalesce(camp.days_since_last_click, {cap}), {cap}) AS days_since_last_click,
           coalesce(svc.contacts_180d, 0) AS contacts_180d, coalesce(cmp.complaints_180d, 0) AS complaints_180d,
           coalesce(dig.logins_90d, 0) AS logins_90d, coalesce(dig.digital_active_days_90d, 0) AS digital_active_days_90d,
           least(coalesce(dig.days_since_last_login, {cap}), {cap}) AS days_since_last_login,
           tx.last_ts_used{fut_cols}
    FROM pop
    JOIN src_customers c ON c.customer_id = pop.customer_id
    JOIN tx ON tx.customer_id = pop.customer_id AND tx.T = pop.T
    LEFT JOIN dec ON dec.customer_id = pop.customer_id AND dec.T = pop.T
    LEFT JOIN prod ON prod.customer_id = pop.customer_id AND prod.T = pop.T
    LEFT JOIN camp ON camp.customer_id = pop.customer_id AND camp.T = pop.T
    LEFT JOIN svc ON svc.customer_id = pop.customer_id AND svc.T = pop.T
    LEFT JOIN cmp ON cmp.customer_id = pop.customer_id AND cmp.T = pop.T
    LEFT JOIN dig ON dig.customer_id = pop.customer_id AND dig.T = pop.T
    {fut_join}
    ORDER BY snapshot_date, customer_id
    """).df()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    if with_labels:
        df[config.TARGET_NAME] = (df["fut_txn_count"] < config.LOW_ENGAGEMENT_BELOW).astype(int)
    check_feature_rows(df)
    return df


def check_feature_rows(df: pd.DataFrame) -> None:
    """The frozen schema is present and no feature used data at or after its cutoff."""
    missing = [f for f in config.FEATURES if f not in df.columns]
    if missing:
        raise ValueError(f"feature columns missing: {missing}")
    cutoffs = pd.to_datetime(df["snapshot_date"])
    if (pd.to_datetime(df["last_ts_used"]) >= cutoffs).any():
        raise ValueError("a feature row used data at or after its cutoff")


def model_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """The model inputs, in training order, as float (what LightGBM receives)."""
    return df[config.FEATURES].astype(float)
