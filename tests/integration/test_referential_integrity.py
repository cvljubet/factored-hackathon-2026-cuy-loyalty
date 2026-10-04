"""P0 referential integrity: orphan and cross-table flags in silver, the foreign key and
rule reports, the orphan threshold, and gold's exclusions. Planted cases: lake_fixture.py.

Run with: uv run --no-project --python 3.11 --with pyspark==3.5.4 --with pytest pytest tests/integration
"""
import shutil

import pytest
from pyspark.sql import functions as F

import bronze_to_silver
from bronze_to_silver import TABLES, build_order
from dq_rules import FOREIGN_KEYS


def reasons(spark, lake, table, key):
    """{primary key: sorted dq_reasons} of a silver table."""
    rows = spark.read.parquet(f"{lake}/silver/{table}/").select(key, "dq_reasons").collect()
    return {r[0]: sorted(r[1]) for r in rows}


def latest(spark, path, schema=None):
    """Rows of a report's last run."""
    reader = spark.read.schema(schema) if schema else spark.read
    df = reader.json(path)
    return df.where(F.col("run_at") == df.agg(F.max("run_at")).first()[0]).collect()


def test_build_order_puts_parents_first():
    order = build_order(list(TABLES))
    assert sorted(order) == sorted(TABLES)
    for fk in FOREIGN_KEYS:
        assert order.index(fk.parent) < order.index(fk.child), fk


def test_orphans_and_mismatches_are_flagged_not_dropped(spark, lake):
    assert reasons(spark, lake, "transactions", "transaction_id") == {
        "T1": [],
        "T2": ["product_owner_mismatch"],
        "T3": ["before_product_opening"],
        "T4": ["orphan_products"],
        "T5": ["orphan_products"],
        "T6": ["orphan_customers"],
        "T7": ["orphan_branches"],
        "T8": [],
    }
    assert reasons(spark, lake, "call_transcripts", "transcript_id")["R3"] == ["interaction_customer_mismatch"]
    assert reasons(spark, lake, "satisfaction_surveys", "survey_id")["S3"] == ["interaction_customer_mismatch"]
    assert reasons(spark, lake, "complaints", "complaint_id")["Q4"] == ["orphan_branches", "orphan_service_agents"]


def test_null_is_an_orphan_only_in_not_null_columns(spark, lake):
    assert reasons(spark, lake, "customers", "customer_id")["C4"] == ["orphan_branches"]
    assert reasons(spark, lake, "call_transcripts", "transcript_id")["R4"] == ["orphan_service_agents"]
    assert reasons(spark, lake, "digital_events", "event_id")["E2"] == []
    assert reasons(spark, lake, "satisfaction_surveys", "survey_id")["S2"] == []
    assert reasons(spark, lake, "service_agents", "agent_id")["A2"] == []


def test_every_table_gets_validity_columns_without_null_flags(spark, lake):
    for table in TABLES:
        df = spark.read.parquet(f"{lake}/silver/{table}/")
        flags = [c for c in df.columns if c.startswith("dq_invalid_")]
        assert df.where((F.size("dq_reasons") == 0) != F.col("dq_is_valid")).count() == 0, table
        for c in flags:
            assert df.where(F.col(c).isNull()).count() == 0, (table, c)


def test_fk_report(spark, lake):
    report = {(r.child_table, r.child_column): r for r in latest(spark, f"{lake}/silver/_fk_report/")}
    assert len(report) == len(FOREIGN_KEYS) == 24

    product = report[("transactions", "product_id")]  # NOT NULL: the null T5 counts as an orphan
    assert (product.child_rows, product.null_rows, product.checked_rows, product.orphan_rows) == (8, 1, 8, 2)
    assert product.orphan_pct == 25.0 and product.status == "warn"

    branch = report[("transactions", "branch_id")]  # nullable: only T1 and T7 have a value
    assert (branch.null_rows, branch.checked_rows, branch.orphan_rows, branch.orphan_pct) == (6, 2, 1, 50.0)

    assert report[("complaints", "origin_interaction_id")].status == "not_checkable"
    assert report[("customers", "registration_branch_id")].orphan_rows == 2
    assert report[("products", "opening_branch_id")].orphan_rows == 1
    assert report[("call_center_interactions", "agent_id")].status == "warn"
    assert report[("satisfaction_surveys", "agent_id")].status == "ok"


def test_rule_report(spark, lake):
    report = {(r.table, r.rule): r for r in latest(spark, f"{lake}/silver/_rule_report/")}
    assert report[("transactions", "product_owner_mismatch")].failing_rows == 1
    assert report[("transactions", "orphan_products")].failing_pct == 25.0
    assert report[("customers", "credit_score_out_of_range")].failing_rows == 1
    assert report[("satisfaction_surveys", "score_out_of_range")].failing_rows == 1


def test_job_fails_above_threshold(spark, lake, tmp_path):
    copy = tmp_path / "lake"
    shutil.copytree(lake, copy)
    with pytest.raises(RuntimeError, match=r"campaign_sends\.customer_id .*campaign_sends\.campaign_id"):
        bronze_to_silver.main(["bronze_to_silver.py", "--lake_bucket", str(copy), "--tables", "campaign_sends"])
    # The table and the reports are written before the job fails, so the report explains it.
    statuses = {r.child_column: r.status for r in latest(spark, f"{copy}/silver/_fk_report/")}
    assert statuses == {"customer_id": "fail", "campaign_id": "fail"}


def test_gold_leaves_out_orphaned_customers_and_products_and_counts_them(spark, lake):
    schema = "table string, rows_in long, rows_excluded long, excluded_by_reason map<string,long>, run_at timestamp"
    exclusions = {r.table: r for r in latest(spark, f"{lake}/gold/_exclusions/", schema)}
    txn = exclusions["transactions"]
    assert (txn.rows_in, txn.rows_excluded) == (8, 3)
    assert txn.excluded_by_reason == {"orphan_customers": 1, "orphan_products": 2}
    assert exclusions["satisfaction_surveys"].excluded_by_reason == {"orphan_customers": 1, "score_out_of_range": 1}
    assert "customers" not in exclusions

    c1 = spark.read.parquet(f"{lake}/gold/customer_features/").where("customer_id = 'C1'").first()
    assert c1.txn_count_90d == 3  # T1, T7, T8: the orphan branch on T7 doesn't remove it
    assert c1.n_campaign_sends == 2  # N2's campaign doesn't exist, but the send stays
    customers = spark.read.parquet(f"{lake}/gold/customer_360/").select("customer_id").collect()
    assert sorted(r[0] for r in customers) == ["C1", "C2", "C3", "C4", "C5"]
