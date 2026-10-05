"""P2 late arrivals and volumes: warnings only, never a failed run (the session's job run warns on
every table, the fixture being tiny). Planted cases: lake_fixture.py.

Run with: see tests/integration/requirements.txt
"""
import datetime

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


def test_lag_is_measured_from_the_business_day(spark, lake):
    def lag(table, key, value):
        row = silver_row(spark, lake, table, f"{key} = '{value}'")
        return row.arrival_lag_days, str(row.business_date), "processed_before_event" in row.dq_reasons

    assert lag("transactions", "transaction_id", "T4") == (0, "2026-06-01", False)  # 03:00, before 06:00
    assert lag("complaints", "complaint_id", "Q4") == (0, "2026-05-03", False)  # exactly 08:00:00
    assert lag("digital_events", "event_id", "E4") == (0, "2026-05-02", False)  # its session began at 05:30
    # Surveys follow their interaction: S1 answered on the 2nd about I1 of the 1st, processed on the 2nd.
    assert lag("satisfaction_surveys", "survey_id", "S1") == (1, "2026-05-01", False)
    s2 = silver_row(spark, lake, "satisfaction_surveys", "survey_id = 'S2'")  # no interaction: nothing to measure
    assert (s2.business_date, s2.arrival_lag_days) == (None, None)


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


def test_unusual_days_compare_with_the_same_weekday():
    # Four weeks (2026-01-05 is a Monday): weekdays carry 100 rows, weekends 40.
    rows_per_day = {}
    for week in range(4):
        for d in range(7):
            day = datetime.date(2026, 1, 5) + datetime.timedelta(days=7 * week + d)
            rows_per_day[str(day)] = 40 if d >= 5 else 100
    assert unusual_days(rows_per_day) == []  # quiet weekends are normal
    rows_per_day["2026-01-13"] = 250  # a Tuesday at 2.5x the Tuesday median
    rows_per_day["2026-01-18"] = 15  # a Sunday at well under half the Sunday median
    assert unusual_days(rows_per_day) == ["2026-01-13:250", "2026-01-18:15"]
    assert unusual_days({}) == []


EXPECTED_TABLES = [
    "customers", "products", "branches", "service_agents", "marketing_campaigns", "transactions",
    "call_center_interactions", "call_transcripts", "satisfaction_surveys", "digital_events", "complaints",
    "campaign_sends", "daily_exchange_rates",
]
