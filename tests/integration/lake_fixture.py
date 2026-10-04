"""A tiny bronze lake for the Glue job tests: valid rows plus one planted case per rule.

Ids ending in 9 (C9, P9, B9, A9, I9, M9) exist in no parent table. Only the columns
the jobs read are written; the rest of the dictionary's columns are left out.
"""
import csv
from pathlib import Path

COLUMNS = {
    "branches": [
        "branch_id", "country", "branch_opening_date", "has_atms", "atm_count", "has_teller_windows",
        "teller_window_count", "latitude", "longitude",
    ],
    "service_agents": ["agent_id", "assigned_branch_id", "hire_date", "avg_csat", "total_monthly_interactions"],
    "marketing_campaigns": ["campaign_id", "start_date", "end_date", "budget", "expected_conversion_rate"],
    "customers": [
        "customer_id", "first_name", "last_name", "email", "mobile_phone", "date_of_birth", "city", "state",
        "country", "segment", "credit_score", "estimated_monthly_income", "registration_date",
        "registration_branch_id", "customer_status", "last_updated", "accepts_marketing",
    ],
    "products": [
        "product_id", "customer_id", "product_type", "currency", "current_balance", "credit_limit",
        "interest_rate", "opening_date", "expiration_date", "opening_branch_id", "product_status",
        "has_linked_app", "days_past_due", "last_transaction_date", "last_updated",
    ],
    "transactions": [
        "transaction_id", "transaction_date", "process_date", "product_id", "customer_id", "transaction_type",
        "transaction_category", "amount", "currency", "amount_usd", "branch_id", "transaction_country",
        "transaction_status", "is_fraud", "fraud_score", "latitude", "longitude",
    ],
    "call_center_interactions": [
        "interaction_id", "interaction_date", "process_date", "customer_id", "agent_id", "contact_reason",
        "reason_category", "duration_seconds", "wait_time_seconds", "was_resolved", "requires_followup",
        "sentiment_score", "was_escalated", "has_transcript", "has_recording",
    ],
    "call_transcripts": [
        "transcript_id", "interaction_id", "process_date", "customer_id", "agent_id", "accent_confidence",
        "duration_seconds",
    ],
    "satisfaction_surveys": [
        "survey_id", "survey_date", "process_date", "interaction_id", "customer_id", "agent_id", "survey_type",
        "main_score", "response_time_hours",
    ],
    "digital_events": [
        "event_id", "event_date", "process_date", "customer_id", "product_id", "event_value", "duration_seconds",
        "is_mobile",
    ],
    "complaints": [
        "complaint_id", "creation_date", "process_date", "customer_id", "affected_product_id", "related_branch_id",
        "origin_interaction_id", "assigned_agent_id", "assignment_date",
    ],
    "campaign_sends": ["send_id", "send_date", "process_date", "campaign_id", "customer_id"],
    "daily_exchange_rates": ["date", "source_currency", "target_currency", "exchange_rate", "buy_rate", "sell_rate"],
}


def rows(defaults: dict, *overrides: dict) -> list:
    return [{**defaults, **o} for o in overrides]


ROWS = {
    "branches": rows(
        {"country": "Colombia", "branch_opening_date": "2010-01-01", "has_atms": "true", "atm_count": "2"},
        {"branch_id": "B1"},
        {"branch_id": "B2"},
    ),
    "service_agents": rows(
        {"hire_date": "2020-01-01"},
        {"agent_id": "A1", "assigned_branch_id": "B1"},
        {"agent_id": "A2"},  # no branch: allowed, the column is nullable
        {"agent_id": "A3", "assigned_branch_id": "B9"},  # orphan_branches
    ),
    "marketing_campaigns": [{"campaign_id": "M1", "start_date": "2026-01-01", "end_date": "2026-12-31"}],
    "customers": rows(
        {
            "first_name": "Ana", "last_name": "Ruiz", "date_of_birth": "1990-01-01", "city": "Bogota",
            "state": "Cundinamarca", "country": "Colombia", "segment": "Basic", "credit_score": "700",
            "registration_date": "2023-07-01 09:00:00", "registration_branch_id": "B1",
            "customer_status": "Active", "last_updated": "2026-01-01 09:00:00", "accepts_marketing": "true",
        },
        {"customer_id": "C1"},
        {"customer_id": "C2", "registration_branch_id": "B2"},
        {"customer_id": "C3", "registration_branch_id": "B9"},  # orphan_branches
        {"customer_id": "C4", "registration_branch_id": ""},  # orphan_branches: null in a NOT NULL key
        {"customer_id": "C5", "credit_score": "900"},  # credit_score_out_of_range
    ),
    "products": rows(
        {
            "product_type": "Cuenta Ahorro", "currency": "COP", "current_balance": "1000", "opening_date": "2024-01-01",
            "opening_branch_id": "B1", "product_status": "Active", "has_linked_app": "true",
            "last_updated": "2026-01-01 09:00:00",
        },
        {"product_id": "P1", "customer_id": "C1"},
        {"product_id": "P2", "customer_id": "C2", "opening_date": "2026-01-01"},
        {"product_id": "P3", "customer_id": "C9"},  # orphan_customers
        {"product_id": "P4", "customer_id": "C1", "opening_branch_id": "B9"},  # orphan_branches
    ),
    # Customer C1's spend in gold counts T1, T7 and T8 only: T4 and T5 are left out.
    "transactions": rows(
        {
            "transaction_type": "Purchase", "transaction_category": "Food", "amount": "100000", "currency": "COP",
            "transaction_country": "Colombia", "transaction_status": "Approved", "is_fraud": "false",
        },
        {"transaction_id": "T1", "transaction_date": "2026-06-01 12:00:00", "product_id": "P1", "customer_id": "C1",
         "branch_id": "B1"},
        # product_owner_mismatch: P1 belongs to C1
        {"transaction_id": "T2", "transaction_date": "2026-06-03 12:00:00", "product_id": "P1", "customer_id": "C2"},
        # before_product_opening: P2 opened 2026-01-01
        {"transaction_id": "T3", "transaction_date": "2025-12-15 12:00:00", "product_id": "P2", "customer_id": "C2"},
        # orphan_products
        {"transaction_id": "T4", "transaction_date": "2026-06-02 12:00:00", "product_id": "P9", "customer_id": "C1"},
        # orphan_products: null in a NOT NULL key
        {"transaction_id": "T5", "transaction_date": "2026-06-02 12:00:00", "product_id": "", "customer_id": "C1"},
        # orphan_customers (P3's owner is C9 too, so no owner mismatch)
        {"transaction_id": "T6", "transaction_date": "2026-06-04 12:00:00", "product_id": "P3", "customer_id": "C9"},
        # orphan_branches: kept in gold
        {"transaction_id": "T7", "transaction_date": "2026-06-05 12:00:00", "product_id": "P1", "customer_id": "C1",
         "branch_id": "B9"},
        {"transaction_id": "T8", "transaction_date": "2026-06-10 12:00:00", "product_id": "P1", "customer_id": "C1"},
    ),
    "call_center_interactions": rows(
        {
            "interaction_date": "2026-05-01 10:00:00", "contact_reason": "Consulta", "reason_category": "Producto",
            "requires_followup": "false", "was_escalated": "false", "has_transcript": "true", "has_recording": "true",
        },
        {"interaction_id": "I1", "customer_id": "C1", "agent_id": "A1"},
        {"interaction_id": "I2", "customer_id": "C2"},  # no agent: allowed, the column is nullable
        {"interaction_id": "I3", "customer_id": "C9", "agent_id": "A1"},  # orphan_customers
        {"interaction_id": "I4", "customer_id": "C1", "agent_id": "A9"},  # orphan_service_agents
    ),
    "call_transcripts": rows(
        {"duration_seconds": "120"},
        {"transcript_id": "R1", "interaction_id": "I1", "customer_id": "C1", "agent_id": "A1"},
        {"transcript_id": "R2", "interaction_id": "I9", "customer_id": "C1", "agent_id": "A1"},  # orphan interaction
        # interaction_customer_mismatch: I1 is C1's
        {"transcript_id": "R3", "interaction_id": "I1", "customer_id": "C2", "agent_id": "A1"},
        # orphan_service_agents: null in a NOT NULL key
        {"transcript_id": "R4", "interaction_id": "I2", "customer_id": "C2"},
    ),
    "satisfaction_surveys": rows(
        {"survey_date": "2026-05-02 10:00:00"},
        {"survey_id": "S1", "interaction_id": "I1", "customer_id": "C1", "agent_id": "A1", "survey_type": "CSAT",
         "main_score": "4"},
        # no interaction and no agent: allowed, both columns are nullable
        {"survey_id": "S2", "customer_id": "C2", "survey_type": "NPS", "main_score": "9"},
        # interaction_customer_mismatch: I2 is C2's
        {"survey_id": "S3", "interaction_id": "I2", "customer_id": "C1", "agent_id": "A1", "survey_type": "CSAT",
         "main_score": "5"},
        # score_out_of_range
        {"survey_id": "S4", "interaction_id": "I1", "customer_id": "C1", "agent_id": "A1", "survey_type": "CSAT",
         "main_score": "7"},
        # orphan_customers
        {"survey_id": "S5", "customer_id": "C9", "survey_type": "NPS", "main_score": "8"},
    ),
    "digital_events": rows(
        {"event_date": "2026-05-03 10:00:00", "is_mobile": "true"},
        {"event_id": "E1", "customer_id": "C1", "product_id": "P1"},
        {"event_id": "E2"},  # anonymous, no product: allowed, both columns are nullable
        {"event_id": "E3", "customer_id": "C9"},  # orphan_customers
        {"event_id": "E4", "customer_id": "C1", "product_id": "P9"},  # orphan_products
    ),
    # origin_interaction_id is blank in every row, as in the source.
    "complaints": rows(
        {"creation_date": "2026-05-04 10:00:00"},
        {"complaint_id": "Q1", "customer_id": "C1", "affected_product_id": "P1", "related_branch_id": "B1",
         "assigned_agent_id": "A1"},
        {"complaint_id": "Q2", "customer_id": "C9"},  # orphan_customers
        {"complaint_id": "Q3", "customer_id": "C1", "affected_product_id": "P9"},  # orphan_products
        # orphan_branches and orphan_service_agents
        {"complaint_id": "Q4", "customer_id": "C2", "related_branch_id": "B9", "assigned_agent_id": "A9"},
    ),
    "campaign_sends": rows(
        {"send_date": "2026-05-05 10:00:00"},
        {"send_id": "N1", "campaign_id": "M1", "customer_id": "C1"},
        {"send_id": "N2", "campaign_id": "M9", "customer_id": "C1"},  # orphan_marketing_campaigns: kept in gold
        {"send_id": "N3", "campaign_id": "M1", "customer_id": "C9"},  # orphan_customers
    ),
    "daily_exchange_rates": rows(
        {"date": "2026-06-01"},
        {"source_currency": "COP", "target_currency": "USD", "exchange_rate": "0.00025"},
        {"source_currency": "USD", "target_currency": "COP", "exchange_rate": "4000"},
    ),
}


def write_bronze(root: Path, ingest_date: str = "2026-10-01") -> None:
    for table, table_rows in ROWS.items():
        folder = root / "bronze" / table / f"ingest_date={ingest_date}"
        folder.mkdir(parents=True)
        with open(folder / f"{table}.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS[table], restval="")
            writer.writeheader()
            writer.writerows(table_rows)
