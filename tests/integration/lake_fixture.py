"""A tiny bronze lake for the Glue job tests: valid rows plus one planted case per rule.

Ids ending in 9 (C9, P9, B9, A9, I9, M9) exist in no parent table. Files carry the full
header the silver job expects (COLUMNS); columns a row doesn't set are left blank.
"""
import csv
import datetime
from pathlib import Path

from bronze_to_silver import COLUMNS
from dq_rules import EVENT_DATES


def rows(defaults: dict, *overrides: dict) -> list:
    return [{**defaults, **o} for o in overrides]


# One USD buys 4000 COP, 900 ARS or 17 MXN, every day; the bank buys at -1% and sells at +1%.
USD_VALUE = {"USD": 1.0, "COP": 1 / 4000, "ARS": 1 / 900, "MXN": 1 / 17}


def exchange_rates(skip: frozenset = frozenset(), override: dict = None) -> list:
    """Every pair, every day of the dataset, minus the (source, target, date) in skip; override
    replaces fields of one (source, target, date)."""
    out, day = [], datetime.date(2023, 6, 17)
    while day <= datetime.date(2026, 6, 17):
        for a in USD_VALUE:
            for b in USD_VALUE:
                if a == b or (a, b, str(day)) in skip:
                    continue
                rate = USD_VALUE[a] / USD_VALUE[b]
                row = {
                    "date": str(day), "source_currency": a, "target_currency": b, "exchange_rate": f"{rate:.10g}",
                    "buy_rate": f"{rate * 0.99:.10g}", "sell_rate": f"{rate * 1.01:.10g}", "source": "Central Bank",
                }
                out.append({**row, **(override or {}).get((a, b, str(day)), {})})
        day += datetime.timedelta(days=1)
    return out


# The planted FX cases: COP->USD misses two days (filled from the day before); ARS->USD is wrong
# on 2026-06-05 (its inverse and cross rates disagree); MXN->USD buys above the mid on 2026-06-06;
# COP->MXN is 3.5% high on 2026-06-07, within the source's noise (FX_NOISE): not flagged.
FX_GAP = frozenset({("COP", "USD", "2026-06-02"), ("COP", "USD", "2026-06-03")})
FX_OVERRIDES = {
    ("ARS", "USD", "2026-06-05"): {"exchange_rate": "0.002"},
    ("MXN", "USD", "2026-06-06"): {"buy_rate": "0.07"},
    ("COP", "MXN", "2026-06-07"): {
        "exchange_rate": "0.00439875", "buy_rate": "0.0043547625", "sell_rate": "0.0044427375",
    },
}


ROWS = {
    "branches": rows(
        {
            "country": "Colombia", "branch_opening_date": "2010-01-01", "has_atms": "true", "atm_count": "2",
            "latitude": "4.6", "longitude": "-74.1",
        },
        {"branch_id": "B1"},
        # location: "null island"; not_unique: B2 and B3 share a branch_code
        {"branch_id": "B2", "latitude": "0.2", "longitude": "0.3", "branch_code": "S0002"},
        {"branch_id": "B3", "latitude": "4.6", "longitude": "30.0", "branch_code": "S0002"},  # location: Africa
    ),
    "service_agents": rows(
        {"hire_date": "2020-01-01"},
        {"agent_id": "A1", "assigned_branch_id": "B1", "email": ""},  # required_missing: email is NOT NULL
        {"agent_id": "A2"},  # no branch: allowed, the column is nullable
        {"agent_id": "A3", "assigned_branch_id": "B9"},  # unmatched branch: counted, not flagged (known broken)
    ),
    "marketing_campaigns": rows(
        {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        # Objective, product and description filled from the name.
        {"campaign_id": "M1", "campaign_name": "CMP_RET_CC_Jun2026_0001"},
        # name_mismatch: the name says Cross-sell and January 2025.
        {"campaign_id": "M2", "campaign_name": "CMP_XSL_SAV_Jan2025_0002", "campaign_objective": "Retention",
         "promoted_product": "Cuenta Ahorro", "description": "Campaña"},
        {"campaign_id": "M3", "campaign_name": "CMP_ABC_ZZZ_Foo2025_0003"},  # campaign_name: unknown codes
        # GEN: no single product, so promoted_product and description stay empty.
        {"campaign_id": "M4", "campaign_name": "CMP_XSL_GEN_Mar2026_0004"},
    ),
    "customers": rows(
        {
            "first_name": "Ana", "last_name": "Ruiz", "date_of_birth": "1990-01-01", "city": "Bogota",
            "state": "Cundinamarca", "country": "Colombia", "segment": "Basic", "credit_score": "700",
            "registration_date": "2023-07-01 09:00:00", "registration_branch_id": "B1",
            "customer_status": "Active", "last_updated": "2026-01-01 09:00:00", "accepts_marketing": "true",
            "estimated_monthly_income": "3000000",
        },
        {"customer_id": "C1"},
        {"customer_id": "C2", "registration_branch_id": "B2"},
        # last_updated_after_cutoff. registration_branch_id is a known-broken key (dq_rules): C3's
        # unmatched and C4's missing branch are counted in _fk_report, not flagged.
        {"customer_id": "C3", "registration_branch_id": "B9", "last_updated": "2026-08-01 09:00:00"},
        {"customer_id": "C4", "registration_branch_id": ""},
        # credit_score_out_of_range; registration_date after last_updated
        {"customer_id": "C5", "credit_score": "900", "registration_date": "2026-02-01 09:00:00"},
        # Duplicates. C2 twice, identical: an exact duplicate.
        {"customer_id": "C2", "registration_branch_id": "B2"},
        # C1 in three versions: the latest update wins (Medellin); an update after the dataset
        # ends can't win (Cali).
        {"customer_id": "C1", "city": "Medellin", "last_updated": "2026-03-01 09:00:00"},
        {"customer_id": "C1", "city": "Cali", "last_updated": "2026-09-01 09:00:00"},
    ),
    "products": rows(
        {
            "product_type": "Cuenta Ahorro", "currency": "COP", "current_balance": "1000", "opening_date": "2024-01-01",
            "opening_branch_id": "B1", "product_status": "Active", "has_linked_app": "true",
            "last_updated": "2026-01-01 09:00:00",
        },
        {"product_id": "P1", "customer_id": "C1"},  # last_txn_mismatch: no date, but T1, T7, T8 exist
        {"product_id": "P2", "customer_id": "C2", "opening_date": "2026-01-01", "last_transaction_date": "2025-12-15"},
        # orphan_customers
        {"product_id": "P3", "customer_id": "C9", "last_transaction_date": "2026-06-04 12:00:00"},
        # orphan_branches; date_order: it expires before it opened
        {"product_id": "P4", "customer_id": "C1", "opening_branch_id": "B9", "expiration_date": "2020-01-01"},
    )
    # Eleven personal loans, ten with all their terms: the data makes those columns required for
    # loans. L10 lacks expiration_date, L11 lacks interest_rate.
    + rows(
        {
            "product_type": "Préstamo Personal", "customer_id": "C2", "currency": "COP", "current_balance": "500000",
            "opening_date": "2024-01-01", "opening_branch_id": "B1", "product_status": "Active",
            "has_linked_app": "false", "last_updated": "2026-01-01 09:00:00", "interest_rate": "18.5",
            "days_past_due": "0", "credit_limit": "1000000", "expiration_date": "2029-01-01",
        },
        *[{"product_id": f"L{i:02d}"} for i in range(1, 10)],
        {"product_id": "L10", "expiration_date": ""},
        {"product_id": "L11", "interest_rate": ""},
    ),
    # Customer C1's spend in gold counts T1, T7 and T8 only: T4 and T5 are left out.
    "transactions": rows(
        {
            "transaction_type": "Purchase", "transaction_category": "Food", "amount": "100000", "currency": "COP",
            "transaction_country": "Colombia", "transaction_status": "Approved", "is_fraud": "false",
        },
        {"transaction_id": "T1", "transaction_date": "2026-06-01 12:00:00", "product_id": "P1", "customer_id": "C1",
         "branch_id": "B1", "latitude": "4.6", "longitude": "-74.1"},
        # product_owner_mismatch: P1 belongs to C1. In Madrid, which is fine for a Spain transaction.
        # Processed 10 days after the event: a late arrival.
        {"transaction_id": "T2", "transaction_date": "2026-06-03 12:00:00", "product_id": "P1", "customer_id": "C2",
         "transaction_country": "Spain", "latitude": "40.4", "longitude": "-3.7", "process_date": "2026-06-13"},
        # before_product_opening: P2 opened 2026-01-01; location: Madrid for a Colombia transaction
        {"transaction_id": "T3", "transaction_date": "2025-12-15 12:00:00", "product_id": "P2", "customer_id": "C2",
         "latitude": "40.4", "longitude": "-3.7"},
        # orphan_products. At 03:00, before the 06:00 business day starts: processed on the 1st, on time.
        {"transaction_id": "T4", "transaction_date": "2026-06-02 03:00:00", "product_id": "P9", "customer_id": "C1",
         "process_date": "2026-06-01"},
        # orphan_products: null in a NOT NULL key
        {"transaction_id": "T5", "transaction_date": "2026-06-02 12:00:00", "product_id": "", "customer_id": "C1"},
        # orphan_customers (P3's owner is C9 too, so no owner mismatch); delivered in 2026-06-20's file
        {"transaction_id": "T6", "transaction_date": "2026-06-04 12:00:00", "product_id": "P3", "customer_id": "C9",
         "_file_day": "2026-06-20"},
        # orphan_branches: kept in gold. In USD with amount_usd 0, as in the source: amount_usd becomes
        # 50. C1's home currency is COP, so this purchase costs C1 50 x (4040 - 4000) COP = 0.5 USD.
        {"transaction_id": "T7", "transaction_date": "2026-06-05 12:00:00", "product_id": "P1", "customer_id": "C1",
         "branch_id": "B9", "currency": "USD", "amount": "50", "amount_usd": "0"},
        {"transaction_id": "T8", "transaction_date": "2026-06-10 12:00:00", "product_id": "P1", "customer_id": "C1"},
        # Duplicate: T8 again with a later process_date wins over the one without.
        {"transaction_id": "T8", "transaction_date": "2026-06-10 12:00:00", "product_id": "P1", "customer_id": "C1",
         "process_date": "2026-06-11", "amount": "200000"},
    ),
    "call_center_interactions": rows(
        {
            "interaction_date": "2026-05-01 10:00:00", "contact_reason": "Producto", "reason_category": "Producto",
            "requires_followup": "false", "was_escalated": "false", "has_transcript": "true", "has_recording": "true",
            "interaction_type": "Inbound Call", "channel": "Phone", "duration_seconds": "300",
        },
        {"interaction_id": "I1", "customer_id": "C1", "agent_id": "A1"},
        # no agent: allowed, the column is nullable; missing_duration on a phone call
        {"interaction_id": "I2", "customer_id": "C2", "duration_seconds": ""},
        {"interaction_id": "I3", "customer_id": "C9", "agent_id": "A1"},  # orphan_customers
        {"interaction_id": "I4", "customer_id": "C1", "agent_id": "A9"},  # orphan_service_agents
        # An email has no duration: fine.
        {"interaction_id": "I5", "customer_id": "C2", "interaction_type": "Email", "channel": "Email",
         "duration_seconds": ""},
    ),
    "call_transcripts": rows(
        {"duration_seconds": "120"},
        {"transcript_id": "R1", "interaction_id": "I1", "customer_id": "C1", "agent_id": "A1",
         "mentioned_entities": '{"account_numbers": 1, "dates": 0, "amounts": 2, "products": "Cuenta Ahorro"}'},
        # orphan interaction; entities_json; missing_duration
        {"transcript_id": "R2", "interaction_id": "I9", "customer_id": "C1", "agent_id": "A1",
         "mentioned_entities": "{not json", "duration_seconds": ""},
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
        # nps_category: a 3 is a Detractor; a CSAT survey has no NPS category.
        {"survey_id": "S6", "customer_id": "C2", "survey_type": "NPS", "main_score": "3", "nps_category": "Promoter"},
        {"survey_id": "S7", "customer_id": "C2", "survey_type": "CSAT", "main_score": "4", "nps_category": "Passive"},
    ),
    "digital_events": rows(
        {"event_date": "2026-05-03 10:00:00", "is_mobile": "true"},
        # out_of_range. E1 starts session SES-A at 05:30, before the 06:00 business day: the whole
        # session, E4 at 06:05 included, has the previous day's process_date, on time.
        {"event_id": "E1", "customer_id": "C1", "product_id": "P1", "duration_seconds": "-5", "session_id": "SES-A",
         "event_date": "2026-05-03 05:30:00", "process_date": "2026-05-02"},
        {"event_id": "E2"},  # anonymous, no product: not orphans (nullable keys), but missing_customer
        {"event_id": "E3", "customer_id": "C9"},  # orphan_customers
        {"event_id": "E4", "customer_id": "C1", "product_id": "P9", "session_id": "SES-A",  # orphan_products
         "event_date": "2026-05-03 06:05:00", "process_date": "2026-05-02"},
        # No key: kept, flagged missing_key, not merged with each other.
        {"event_id": "", "customer_id": "C1", "product_id": "P1"},
        {"event_id": "", "customer_id": "C2"},
    ),
    # origin_interaction_id is blank in every row, as in the source.
    "complaints": rows(
        {"creation_date": "2026-05-04 10:00:00"},
        # processed_before_event: processed the day before it was created
        {"complaint_id": "Q1", "customer_id": "C1", "affected_product_id": "P1", "related_branch_id": "B1",
         "assigned_agent_id": "A1", "process_date": "2026-05-03"},
        {"complaint_id": "Q2", "customer_id": "C9"},  # orphan_customers
        # orphan_products; outside_dataset: created after the data ends
        {"complaint_id": "Q3", "customer_id": "C1", "affected_product_id": "P9",
         "creation_date": "2027-01-01 10:00:00"},
        # orphan_branches and orphan_service_agents
        # At exactly 08:00:00, when the business day starts: either day is on time.
        {"complaint_id": "Q4", "customer_id": "C2", "related_branch_id": "B9", "assigned_agent_id": "A9",
         "creation_date": "2026-05-04 08:00:00", "process_date": "2026-05-03"},
    ),
    "campaign_sends": rows(
        {"send_date": "2026-05-05 10:00:00", "send_status": "Sent", "was_delivered": "true", "was_clicked": "false"},
        # sent, was_opened blank: imputed false; not_allowed: there is no Fax channel.
        # Subject "en nan": filled with M1's product, which silver takes from the campaign name.
        {"send_id": "N1", "campaign_id": "M1", "customer_id": "C1", "send_channel": "Fax",
         "subject": "¡Oferta especial en nan!"},
        # orphan_marketing_campaigns: kept in gold. Subject "en nan" with no campaign to fill it: null.
        {"send_id": "N2", "campaign_id": "M9", "customer_id": "C1", "was_opened": "true",
         "open_date": "2026-05-06 10:00:00", "subject": "¡Oferta especial en nan!"},
        {"send_id": "N3", "campaign_id": "M1", "customer_id": "C9"},  # orphan_customers
        # undelivered_engagement and event_order: failed, yet opened, a day before it was sent
        {"send_id": "N4", "campaign_id": "M1", "customer_id": "C2", "send_status": "Failed", "was_delivered": "false",
         "was_opened": "true", "open_date": "2026-05-04 10:00:00", "subject": "¡Oferta especial en Inversión!"},
    ),
    "daily_exchange_rates": exchange_rates(FX_GAP, FX_OVERRIDES),
}


def write_csv(path: Path, header: list, table_rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, restval="")
        writer.writeheader()
        writer.writerows(table_rows)


# Daily tables keep the source's year=/month=/day= folders in bronze, as ingest_to_bronze.sh copies them.
DAILY = {
    "transactions", "call_center_interactions", "call_transcripts", "satisfaction_surveys", "digital_events",
    "complaints", "campaign_sends", "daily_exchange_rates",
}


def bronze_file(root: Path, table: str, ingest_date: str = "2026-10-01", day: str = "2026-06-01") -> Path:
    folder = root / "bronze" / table / f"ingest_date={ingest_date}"
    if table in DAILY:
        y, m, d = day.split("-")
        return folder / f"year={y}" / f"month={m}" / f"day={d}" / f"{table}_{y}{m}{d}.csv"
    return folder / f"{table}.csv"


# A later ingest: its version of P2 wins although its last_updated is older.
LATER_ROWS = {
    "products": rows(
        {**ROWS["products"][1], "current_balance": "5000", "last_updated": "2025-01-01 09:00:00"},
        {},
    ),
}


# Values for the contract's required columns, used where a row doesn't set the column itself, so
# that rows only break the rules they were planted for. Natural keys that must be unique are
# derived from each row's id.
CONTRACT_DEFAULTS = {
    "customers": {"document_type": "DNI"},
    "products": {"opening_channel": "Branch"},
    "branches": {
        "branch_name": "Sucursal", "branch_type": "Main", "address": "Calle 1", "city": "Bogotá",
        "state": "Cundinamarca", "geographic_zone": "Urbana", "phone": "6011234567", "opening_time": "08:00:00",
        "closing_time": "17:00:00", "has_teller_windows": "true", "branch_status": "Active",
    },
    "service_agents": {
        "first_name": "Luis", "last_name": "Gómez", "email": "agente@banco.com", "native_accent": "colombian",
        "country_of_origin": "Colombia", "agent_type": "Phone", "experience_level": "Junior", "languages": "español",
        "agent_status": "Active", "work_shift": "Morning",
    },
    "marketing_campaigns": {"campaign_type": "Email", "campaign_status": "Active"},
    "transactions": {"channel": "POS"},
    "call_transcripts": {
        "process_date": "2026-05-01", "full_text": "Hola", "detected_language": "es",
        "transcription_model": "Whisper v3",
    },
    "satisfaction_surveys": {"send_channel": "Email"},
    "digital_events": {
        "session_id": "SES-1", "event_type": "PageView",
        "event_category": "Navigation", "channel": "Android App",
    },
    "complaints": {
        "case_type": "Complaint", "category": "Service", "reception_channel": "Web",
        "description": "Queja", "priority": "Low", "status": "Open", "sla_breached": "false",
        "is_repeat_complainer": "false",
    },
    "campaign_sends": {"send_channel": "Email", "had_conversion": "false"},
}
NATURAL_KEYS = {
    "customers": ("document_number", "customer_id"),
    "products": ("product_number", "product_id"),
    "branches": ("branch_code", "branch_id"),
    "service_agents": ("employee_code", "agent_id"),
}


def with_defaults(table: str, row: dict) -> dict:
    """Row plus CONTRACT_DEFAULTS, a unique natural key, and process_date on the event's day (no lag)."""
    out = {**CONTRACT_DEFAULTS.get(table, {}), **row}
    event = EVENT_DATES.get(table)
    if "process_date" in COLUMNS[table] and "process_date" not in out and out.get(event):
        out["process_date"] = out[event][:10]
    if table in NATURAL_KEYS:
        column, key = NATURAL_KEYS[table]
        out.setdefault(column, f"N-{row.get(key)}")
    return out


def write_bronze(root: Path) -> None:
    """Daily tables get one file per day, the day of each row's process_date (or its _file_day, to
    plant a row in the wrong day's file). Exchange rates stay in one file."""
    for table, table_rows in ROWS.items():
        full = [with_defaults(table, r) for r in table_rows]
        if table not in DAILY or table == "daily_exchange_rates":
            write_csv(bronze_file(root, table), COLUMNS[table], full)
            continue
        by_day = {}
        for r in full:
            by_day.setdefault(r.pop("_file_day", None) or r["process_date"], []).append(r)
        for day, day_rows in by_day.items():
            write_csv(bronze_file(root, table, day=day), COLUMNS[table], day_rows)
    for table, table_rows in LATER_ROWS.items():
        rows_later = [with_defaults(table, r) for r in table_rows]
        write_csv(bronze_file(root, table, ingest_date="2026-10-02"), COLUMNS[table], rows_later)
