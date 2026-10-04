"""P2 late arrivals and volumes: warnings only, never a failed run (the session's job run warns on
every table, the fixture being tiny). Planted cases: lake_fixture.py.

Run with: see tests/integration/requirements.txt
"""
from bronze_to_silver import unusual_days
from test_duplicates import silver_row
from test_referential_integrity import latest, reasons


def test_arrival_lag_per_row(spark, lake):
    t2 = silver_row(spark, lake, "transactions", "transaction_id = 'T2'")
    assert (t2.arrival_lag_days, t2.is_late_arrival) == (10, True)
    assert "processed_before_event" not in t2.dq_reasons  # late is not invalid
    t1 = silver_row(spark, lake, "transactions", "transaction_id = 'T1'")
    assert (t1.arrival_lag_days, t1.is_late_arrival) == (0, False)
    t8 = silver_row(spark, lake, "transactions", "transaction_id = 'T8'")
    assert (t8.arrival_lag_days, t8.is_late_arrival) == (1, False)  # 1 day is within LATE_ARRIVAL_DAYS
    # Transcripts have no event date of their own: their interaction's is used.
    assert silver_row(spark, lake, "call_transcripts", "transcript_id = 'R1'").arrival_lag_days == 0


def test_processed_before_the_event_is_invalid(spark, lake):
    assert "processed_before_event" in reasons(spark, lake, "complaints", "complaint_id")["Q1"]


def test_arrival_report(spark, lake):
    report = {r.table: r for r in latest(spark, f"{lake}/silver/_arrival_report/")}
    assert set(report) == {
        "transactions", "call_center_interactions", "call_transcripts", "satisfaction_surveys", "digital_events",
        "complaints", "campaign_sends",
    }
    txn = report["transactions"]
    assert (txn.rows, txn.late_rows, txn.late_pct, txn.lag_max, txn.status) == (8, 1, 12.5, 10, "warn")
    assert (txn.same_day, txn.one_day, txn.eight_to_thirty_days) == (6, 1, 1)
    assert report["complaints"].processed_before_event == 1
    assert report["call_center_interactions"].status == "ok"


def test_volume_report(spark, lake):
    report = {r.table: r for r in latest(spark, f"{lake}/silver/_volume_report/")}
    assert set(report) == set(EXPECTED_TABLES)
    txn = report["transactions"]
    assert (txn.raw_rows, txn.silver_rows, txn.expected_rows) == (9, 8, 5_000_000)
    assert txn.status == "warn" and txn.vs_expected_pct < -20
    assert txn.rows_filed_on_other_day == 1  # T6, in 2026-06-20's file
    assert txn.file_days == 8
    assert "2026-06-12" in txn.missing_day_list and "2026-06-01" not in txn.missing_day_list
    customers = report["customers"]  # a snapshot table: no daily files to check
    assert (customers.file_days, customers.missing_days, customers.rows_filed_on_other_day) == (None, 0, None)
    assert customers.status == "warn"  # only the tolerance can say so: 8 rows against 150,000


def test_unusual_days():
    rows_per_day = {"2026-01-01": 10, "2026-01-02": 10, "2026-01-03": 3, "2026-01-04": 25, "2026-01-05": 12}
    assert unusual_days(rows_per_day) == ["2026-01-03:3", "2026-01-04:25"]  # under half, over twice the median 10
    assert unusual_days({}) == []


EXPECTED_TABLES = [
    "customers", "products", "branches", "service_agents", "marketing_campaigns", "transactions",
    "call_center_interactions", "call_transcripts", "satisfaction_surveys", "digital_events", "complaints",
    "campaign_sends", "daily_exchange_rates",
]
