"""P0 duplicates: one deterministic winner per key, exact vs conflicting duplicates reported,
rows without a key kept and flagged. Planted cases: lake_fixture.py.

Run with: see tests/integration/requirements.txt
"""
import datetime

from pyspark.sql import functions as F

from bronze_to_silver import COLUMNS, TABLES, deduplicate
from test_referential_integrity import latest


def silver_row(spark, lake, table, where):
    rows = spark.read.parquet(f"{lake}/silver/{table}/").where(where).collect()
    assert len(rows) == 1, (table, where, len(rows))
    return rows[0]


def test_one_row_per_key(spark, lake):
    for table, cfg in TABLES.items():
        df = spark.read.parquet(f"{lake}/silver/{table}/").where(~F.col("dq_invalid_missing_key"))
        assert df.count() == df.select(*cfg["pk"]).distinct().count(), table


def test_latest_update_wins_but_not_one_after_the_dataset_ends(spark, lake):
    c1 = silver_row(spark, lake, "customers", "customer_id = 'C1'")
    assert c1.city == "Medellin"
    assert (c1.dq_copies, c1.dq_versions) == (3, 3)


def test_latest_ingest_wins_over_a_later_update(spark, lake):
    p2 = silver_row(spark, lake, "products", "product_id = 'P2'")
    assert float(p2.current_balance) == 5000
    assert p2.ingest_date == datetime.date(2026, 10, 2)


def test_latest_process_date_wins(spark, lake):
    t8 = silver_row(spark, lake, "transactions", "transaction_id = 'T8'")
    assert float(t8.amount) == 200000


def test_exact_copies_are_one_version(spark, lake):
    c2 = silver_row(spark, lake, "customers", "customer_id = 'C2'")
    assert (c2.dq_copies, c2.dq_versions) == (2, 1)


def test_rows_without_a_key_are_kept_and_flagged(spark, lake):
    events = spark.read.parquet(f"{lake}/silver/digital_events/").where("event_id IS NULL").collect()
    assert len(events) == 2
    assert all(e.dq_reasons == ["missing_key"] and e.dq_copies == 1 for e in events)


def test_report_splits_exact_and_conflicting_duplicates(spark, lake):
    report = {r.table: r for r in latest(spark, f"{lake}/silver/_dq_report/")}
    customers = report["customers"]
    assert (customers.rows_in, customers.rows_out, customers.rows_removed) == (8, 5, 3)
    assert (customers.duplicate_keys, customers.exact_duplicates) == (2, 1)
    assert (customers.conflicting_keys, customers.conflicting_duplicates) == (1, 2)
    assert report["products"].conflicting_duplicates == 1
    assert report["digital_events"].rows_removed == 0
    for r in report.values():
        assert r.rows_removed == r.exact_duplicates + r.conflicting_duplicates, r.table


def frame(spark, table, rows):
    """A cleaned-bronze-like DataFrame: all COLUMNS as strings (unset ones null), one ingest and file."""
    full = [{c: None for c in COLUMNS[table]} | r | {"ingest_date": datetime.date(2026, 10, 1), "_source_file": "f.csv"}
            for r in rows]
    schema = ", ".join(f"{c} string" for c in COLUMNS[table]) + ", ingest_date date, _source_file string"
    return spark.createDataFrame(full, schema)


def test_latest_process_date_wins_whatever_the_content(spark):
    """8 keys, each in an older and a newer version: the content hash can't pick all 8 by luck."""
    rows = [
        {"transaction_id": f"T{i}", "process_date": day, "amount": str(i * 10 + j)}
        for i in range(8)
        for j, day in enumerate(["2026-06-10", "2026-06-11"])
    ]
    winners = deduplicate(frame(spark, "transactions", rows), "transactions", TABLES["transactions"]).collect()
    assert sorted(r.process_date for r in winners) == ["2026-06-11"] * 8


def test_winner_does_not_depend_on_row_order(spark):
    """Two versions that tie on ingest, update and process date: the content hash decides."""
    rows = [{"agent_id": "A1", "first_name": n} for n in "XY"]
    cfg = TABLES["service_agents"]
    winners = {
        deduplicate(frame(spark, "service_agents", order), "service_agents", cfg).first().first_name
        for order in (rows, rows[::-1])
    }
    assert len(winners) == 1
