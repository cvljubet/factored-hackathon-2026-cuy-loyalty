"""P1 table-specific rules, numbered as in the data quality plan. Planted cases: lake_fixture.py.

Run with: uv run --no-project --python 3.11 --with pyspark==3.5.4 --with pytest pytest tests/integration
"""
import datetime
import shutil

import pytest
from pyspark.sql import functions as F

import bronze_to_silver
from lake_fixture import COLUMNS, FX_OVERRIDES, bronze_file, exchange_rates, write_csv
from test_duplicates import silver_row
from test_referential_integrity import latest, reasons


def silver(spark, lake, table):
    return spark.read.parquet(f"{lake}/silver/{table}/")


def test_1_branch_locations(spark, lake):
    flagged = reasons(spark, lake, "branches", "branch_id")
    assert flagged == {"B1": [], "B2": ["location"], "B3": ["location"]}


def test_2_interactions_drop_reason_category_and_flag_missing_duration(spark, lake):
    assert "reason_category" not in silver(spark, lake, "call_center_interactions").columns
    flagged = reasons(spark, lake, "call_center_interactions", "interaction_id")
    assert flagged["I2"] == ["missing_duration"]  # a phone call without a duration
    assert flagged["I5"] == []  # an email


def test_3_transcript_entities(spark, lake):
    r1 = silver_row(spark, lake, "call_transcripts", "transcript_id = 'R1'")
    assert (r1.entities_account_numbers, r1.entities_dates, r1.entities_amounts, r1.entities_products) == (
        1, 0, 2, "Cuenta Ahorro"
    )
    assert reasons(spark, lake, "call_transcripts", "transcript_id")["R2"] == [
        "entities_json", "missing_duration", "orphan_call_center_interactions"
    ]


def test_4_campaign_sends(spark, lake):
    n1 = silver_row(spark, lake, "campaign_sends", "send_id = 'N1'")
    assert (n1.was_opened, n1.was_opened_imputed) == (False, True)
    n2 = silver_row(spark, lake, "campaign_sends", "send_id = 'N2'")
    assert (n2.was_opened, n2.was_opened_imputed) == (True, False)
    assert reasons(spark, lake, "campaign_sends", "send_id")["N4"] == ["event_order", "undelivered_engagement"]


def test_5_an_empty_source_column_is_reported_once(spark, lake):
    report = {(r.table, r.rule): r for r in latest(spark, f"{lake}/silver/_rule_report/")}
    origin = report[("complaints", "all_null:origin_interaction_id")]
    assert origin.failing_rows == origin.rows == 4
    assert "origin_interaction_id" in silver(spark, lake, "complaints").columns  # kept: the contract has it


def test_6_customers(spark, lake):
    flagged = reasons(spark, lake, "customers", "customer_id")
    assert flagged["C3"] == ["last_updated_after_cutoff"]
    assert flagged["C5"] == ["credit_score_out_of_range", "registration_date"]
    c3 = silver_row(spark, lake, "customers", "customer_id = 'C3'")
    assert c3.last_updated_clean is None and c3.last_updated is not None
    assert silver_row(spark, lake, "customers", "customer_id = 'C1'").income_currency == "COP"


def test_7_exchange_rates_fill_and_consistency(spark, lake):
    fx = silver(spark, lake, "daily_exchange_rates")
    assert fx.where(F.col("date").between("2023-06-17", "2026-06-17")).count() == 1097 * 12
    filled = fx.where("exchange_rate_imputed").select("source_currency", "target_currency", "date").collect()
    assert sorted((r[0], r[1], str(r[2])) for r in filled) == [
        ("COP", "USD", "2026-06-02"), ("COP", "USD", "2026-06-03")
    ]
    assert fx.where("exchange_rate_imputed").agg(F.min("exchange_rate")).first()[0] == pytest.approx(0.00025)

    def flags(day, a, b):
        row = fx.where(f"date = '{day}' AND source_currency = '{a}' AND target_currency = '{b}'").first()
        return sorted(row.dq_reasons)

    assert "inverse_rate" in flags("2026-06-05", "ARS", "USD")
    assert "inverse_rate" in flags("2026-06-05", "USD", "ARS")
    assert "cross_rate" in flags("2026-06-05", "ARS", "COP")  # ARS->USD x USD->COP no longer matches
    assert flags("2026-06-06", "MXN", "USD") == ["rate_order"]
    assert fx.where("date = '2026-06-04'").where(~F.col("dq_is_valid")).count() == 0


def test_7_exchange_rate_gaps_longer_than_three_days_fail_the_job(lake, tmp_path):
    copy = tmp_path / "lake"
    shutil.copytree(lake, copy)
    gap = frozenset(("MXN", "COP", f"2025-03-0{d}") for d in range(1, 5))
    rates = exchange_rates(gap, FX_OVERRIDES)
    write_csv(bronze_file(copy, "daily_exchange_rates"), COLUMNS["daily_exchange_rates"], rates)
    with pytest.raises(RuntimeError, match=r"MXN->COP \(4 days filled in a row"):
        bronze_to_silver.main(["bronze_to_silver.py", "--lake_bucket", str(copy), "--tables", "daily_exchange_rates"])


def test_8_events_without_customer(spark, lake):
    assert reasons(spark, lake, "digital_events", "event_id")["E2"] == ["missing_customer"]


def test_9_campaign_names(spark, lake):
    m1 = silver_row(spark, lake, "marketing_campaigns", "campaign_id = 'M1'")
    assert (m1.objective_code, m1.product_code, m1.campaign_month, m1.campaign_seq) == (
        "RET", "CC", datetime.date(2026, 6, 1), 1
    )
    assert (m1.campaign_objective, m1.promoted_product) == ("Retention", "Tarjeta Crédito")
    assert m1.description == "Campaña de retention para Tarjeta Crédito"
    assert m1.campaign_objective_imputed and m1.promoted_product_imputed and m1.description_imputed
    m4 = silver_row(spark, lake, "marketing_campaigns", "campaign_id = 'M4'")
    assert (m4.campaign_objective, m4.promoted_product, m4.description) == ("Cross-sell", None, None)
    flagged = reasons(spark, lake, "marketing_campaigns", "campaign_id")
    assert flagged == {"M1": [], "M2": ["name_mismatch"], "M3": ["campaign_name"], "M4": []}


def test_10_products(spark, lake):
    flagged = reasons(spark, lake, "products", "product_id")
    assert flagged["L10"] == ["missing_expiration_date"]
    assert flagged["L11"] == ["incomplete_credit_terms"]
    assert flagged["L01"] == []
    assert flagged["P1"] == ["last_txn_mismatch"]  # no date recorded, but it has transactions
    assert flagged["P2"] == []  # recorded date matches T3
    # Formatted in Spark (UTC): collecting a timestamp converts it to the machine's time zone.
    latest_txn = silver(spark, lake, "products").where("product_id = 'P1'").select(
        F.date_format("last_transaction_date_calc", "yyyy-MM-dd HH:mm")
    )
    assert latest_txn.first()[0] == "2026-06-10 12:00"  # T8


def test_11_nps_category(spark, lake):
    s2 = silver_row(spark, lake, "satisfaction_surveys", "survey_id = 'S2'")
    assert (s2.nps_category, s2.nps_category_imputed) == ("Promoter", True)
    s5 = silver_row(spark, lake, "satisfaction_surveys", "survey_id = 'S5'")
    assert s5.nps_category == "Passive"
    flagged = reasons(spark, lake, "satisfaction_surveys", "survey_id")
    assert flagged["S6"] == ["nps_category"] and flagged["S7"] == ["nps_category"]
    s1 = silver_row(spark, lake, "satisfaction_surveys", "survey_id = 'S1'")
    assert (s1.nps_category, s1.nps_category_imputed) == (None, False)  # CSAT: no category


def test_12_transactions_amount_usd(spark, lake):
    t7 = silver_row(spark, lake, "transactions", "transaction_id = 'T7'")
    assert (t7.amount_usd, t7.amount_usd_imputed) == (50.0, True)
    t1 = silver_row(spark, lake, "transactions", "transaction_id = 'T1'")
    assert (t1.amount_usd, t1.amount_usd_imputed) == (None, False)
    report = {(r.table, r.rule): r.failing_rows for r in latest(spark, f"{lake}/silver/_rule_report/")}
    assert report[("transactions", "imputed:amount_usd")] == 1


def test_gold_conversions_and_fx_cost(spark, lake):
    c1 = spark.read.parquet(f"{lake}/gold/customer_features/").where("customer_id = 'C1'").first()
    assert c1.estimated_monthly_income_usd == pytest.approx(750.0)  # 3,000,000 COP at 4000 per USD
    assert c1.fx_cost_usd_90d == pytest.approx(0.5)  # T7, see lake_fixture
    assert c1.n_product_contacts == 2  # I1 and I4 (contact_reason "Producto")
    c2 = spark.read.parquet(f"{lake}/gold/customer_360/").where("customer_id = 'C2'").first()
    p2 = next(p for p in c2.products if p.product_id == "P2")
    assert p2.current_balance_usd == pytest.approx(1.25)  # 5000 COP
    fx = spark.read.parquet(f"{lake}/gold/fx_daily/").where(
        "date = '2026-06-01' AND source_currency = 'USD' AND target_currency = 'COP'"
    ).first()
    assert (fx.mid_rate, fx.spread_pct) == (pytest.approx(4000), pytest.approx(0.02))
