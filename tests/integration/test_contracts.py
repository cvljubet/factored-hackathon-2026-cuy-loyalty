"""P1 generic rules: the Pandera contracts (contracts.py), their row flags, per-column counts and
Pandera's own report. Planted cases: lake_fixture.py.

Run with: see tests/integration/requirements.txt
"""
import contracts
from bronze_to_silver import CONTRACTS, TABLES
from test_referential_integrity import latest, reasons


def test_every_table_has_a_contract_and_every_check_compiles(spark):  # Spark expressions need a session
    assert sorted(CONTRACTS) == sorted(TABLES)
    for schema in CONTRACTS.values():
        contracts.failing(schema)  # raises on a check with no row-level rule


def test_each_kind_of_check_flags_its_planted_row(spark, lake):
    assert "required_missing" in reasons(spark, lake, "service_agents", "agent_id")["A1"]  # no email
    assert "not_allowed" in reasons(spark, lake, "campaign_sends", "send_id")["N1"]  # Fax
    assert "out_of_range" in reasons(spark, lake, "digital_events", "event_id")["E1"]  # -5 seconds
    assert "outside_dataset" in reasons(spark, lake, "complaints", "complaint_id")["Q3"]  # 2027
    assert "date_order" in reasons(spark, lake, "products", "product_id")["P4"]  # expires before opening
    assert "not_unique" in reasons(spark, lake, "branches", "branch_id")["B3"]  # shared branch_code


def test_keys_are_left_to_their_own_flags(spark, lake):
    # T5 has no product_id (NOT NULL): orphan_products says so, not required_missing as well.
    assert reasons(spark, lake, "transactions", "transaction_id")["T5"] == ["orphan_products"]


def test_rule_report_counts_each_check_per_column(spark, lake):
    report = {(r.table, r.rule): r.failing_rows for r in latest(spark, f"{lake}/silver/_rule_report/")}
    assert report[("service_agents", "required_missing:email")] == 1
    assert report[("campaign_sends", "not_allowed:send_channel")] == 1
    assert report[("branches", "not_unique:branch_code")] == 2
    assert report[("products", "date_order:expiration_date")] == 1
    assert report[("customers", "not_allowed:segment")] == 0  # checks that pass are reported too


def test_pandera_reports_what_failed(spark, lake):
    findings = latest(spark, f"{lake}/silver/_contract_report/")
    failed = {(r.table, r.column, r.check) for r in findings}
    assert ("campaign_sends", "send_channel", "isin(['Email', 'SMS', 'Push', 'WhatsApp', 'Voice'])") in failed
    assert ("branches", "branches", "unique(branch_code)") in failed
    assert ("service_agents", "email", "not_nullable") in failed
    assert not [r for r in findings if r.error_type == "WRONG_DATATYPE"], "silver types match the contracts"
    assert not [r for r in findings if r.table == "customers" and r.column == "segment"]


def test_contract_doc_covers_every_table():
    doc = contracts.render_markdown(CONTRACTS)
    assert all(f"## {t}" in doc for t in TABLES)
    assert "| `credit_score` | IntegerType() |  | 300 to 850 |" in doc
