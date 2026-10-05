"""Data quality rules the Glue jobs apply, kept as data in one place.

Deployed next to the job scripts and put on their Python path with --extra-py-files.
"""
from typing import NamedTuple


class ForeignKey(NamedTuple):
    child: str
    column: str
    parent: str
    parent_column: str
    nullable: bool = False  # the dictionary says "FK" without NOT NULL: a null is not an orphan
    # Why the source's values can't be used as references, for a key the dictionary declares but
    # the data doesn't honour. Its orphans are still reported, but it never warns or fails the run
    # and rows get no orphan flag (every row would have one).
    known_broken: str = ""


# The 24 relationships in the data dictionary's "Foreign Key Relationships" section,
# grouped by parent as listed there. Nullability comes from each child column's constraints.
FOREIGN_KEYS = [
    ForeignKey("products", "customer_id", "customers", "customer_id"),
    ForeignKey("transactions", "customer_id", "customers", "customer_id"),
    ForeignKey("call_center_interactions", "customer_id", "customers", "customer_id"),
    ForeignKey("call_transcripts", "customer_id", "customers", "customer_id"),
    ForeignKey("satisfaction_surveys", "customer_id", "customers", "customer_id"),
    ForeignKey("digital_events", "customer_id", "customers", "customer_id", nullable=True),
    ForeignKey("complaints", "customer_id", "customers", "customer_id"),
    ForeignKey("campaign_sends", "customer_id", "customers", "customer_id"),

    ForeignKey(
        "customers", "registration_branch_id", "branches", "branch_id",
        known_broken="150,000 distinct values for 350 branches: a random id per customer (2026-10-04)",
    ),
    ForeignKey("products", "opening_branch_id", "branches", "branch_id"),
    ForeignKey(
        "service_agents", "assigned_branch_id", "branches", "branch_id", nullable=True,
        known_broken="833 distinct values for 350 branches, 99.8% unmatched: random ids (2026-10-04)",
    ),
    ForeignKey("transactions", "branch_id", "branches", "branch_id", nullable=True),
    ForeignKey("complaints", "related_branch_id", "branches", "branch_id", nullable=True),

    ForeignKey("call_center_interactions", "agent_id", "service_agents", "agent_id", nullable=True),
    ForeignKey("call_transcripts", "agent_id", "service_agents", "agent_id"),
    ForeignKey("satisfaction_surveys", "agent_id", "service_agents", "agent_id", nullable=True),
    ForeignKey("complaints", "assigned_agent_id", "service_agents", "agent_id", nullable=True),

    ForeignKey("transactions", "product_id", "products", "product_id"),
    ForeignKey("digital_events", "product_id", "products", "product_id", nullable=True),
    ForeignKey("complaints", "affected_product_id", "products", "product_id", nullable=True),

    ForeignKey("campaign_sends", "campaign_id", "marketing_campaigns", "campaign_id"),

    ForeignKey("call_transcripts", "interaction_id", "call_center_interactions", "interaction_id"),
    ForeignKey("satisfaction_surveys", "interaction_id", "call_center_interactions", "interaction_id", nullable=True),
    # 100% blank in the source, so the report marks it not_checkable instead of passing it.
    ForeignKey("complaints", "origin_interaction_id", "call_center_interactions", "interaction_id", nullable=True),
]

# The dictionary's date range. A timestamp after DATASET_END can't be true.
DATASET_START = "2023-06-17"
DATASET_END = "2026-06-17"

# (lat_min, lat_max, lon_min, lon_max) per country as the data spells it, generous enough to
# include islands and border towns. Foreign transactions happen in USA, Spain and Brazil.
COUNTRY_BOXES = {
    "México": (14.3, 32.8, -118.5, -86.6),
    "Colombia": (-4.3, 13.6, -82.0, -66.8),
    "Argentina": (-55.2, -21.7, -73.7, -53.5),
    "USA": (18.8, 71.6, -179.9, -66.8),
    "Spain": (27.5, 43.9, -18.3, 4.4),
    "Brazil": (-33.9, 5.4, -74.1, -34.7),
}

# A customer's income and spending are in their country's currency.
HOME_CURRENCY = {"México": "MXN", "Colombia": "COP", "Argentina": "ARS"}

# daily_exchange_rates holds every directed pair of these, every day of the dataset.
CURRENCIES = ["ARS", "COP", "MXN", "USD"]
# Each published rate is up to FX_NOISE off a consistent one: on 2026-10-04, A->B x B->A was within
# 4% of 1 for every pair (p50 1.2%, max 3.99%), which two rates 2% off each explain. A check that
# combines n rates allows (1 + FX_NOISE)^n - 1: 4.04% for the inverse (2 rates), 6.12% for cross
# rates (3). Beyond that, a rate is wrong, not noisy.
FX_NOISE = 0.02
FX_MAX_FILLED_DAYS = 3  # more consecutive missing days than this for a pair fails the silver job

# Campaign names look like CMP_RET_INV_May2025_0146: objective code, product code, month, sequence.
# GEN campaigns promote no single product, so their promoted_product stays empty.
CAMPAIGN_OBJECTIVES = {
    "ACQ": "Acquisition", "XSL": "Cross-sell", "REA": "Reactivation", "RET": "Retention", "UPS": "Up-sell",
}
CAMPAIGN_PRODUCTS = {
    "SAV": "Cuenta Ahorro", "CHK": "Cuenta Corriente", "INV": "Inversión", "MTG": "Préstamo Hipotecario",
    "PL": "Préstamo Personal", "INS": "Seguro", "CC": "Tarjeta Crédito", "GEN": None,
}

# NPS score -> category, inclusive bounds.
NPS_CATEGORIES = [(0, 6, "Detractor"), (7, 8, "Passive"), (9, 10, "Promoter")]

# Product columns that are only expected for some product types. Which types, the data decides:
# a column is required for a product type when at least REQUIRED_FILL_PCT of its rows have it,
# so a missing value there is a gap, not "not applicable".
CREDIT_PRODUCTS = ["Préstamo Hipotecario", "Préstamo Personal", "Tarjeta Crédito"]
CREDIT_TERMS = ["interest_rate", "days_past_due", "credit_limit"]
REQUIRED_FILL_PCT = 90.0

# Orphans as a % of the references that can be checked: non-null values, plus nulls where
# the column is NOT NULL. Above WARN the run logs it; above FAIL the silver job fails.
ORPHAN_WARN_PCT = 1.0
ORPHAN_FAIL_PCT = 5.0

# ---- Data contracts (contracts.py builds one Pandera schema per silver table from these) ----

# Columns the silver job removes on purpose, so the contract doesn't expect them.
DROPPED_COLUMNS = {"call_center_interactions": ["reason_category"]}  # a copy of contact_reason

# NOT NULL columns, from each column's constraints in the dictionary.
REQUIRED = {
    "customers": [
        "customer_id", "document_number", "document_type", "first_name", "last_name", "date_of_birth", "city",
        "state", "country", "segment", "registration_date", "registration_branch_id", "customer_status",
        "last_updated", "accepts_marketing",
    ],
    "products": [
        "product_id", "customer_id", "product_type", "product_number", "currency", "current_balance",
        "opening_date", "opening_branch_id", "product_status", "opening_channel", "has_linked_app", "last_updated",
    ],
    "branches": [
        "branch_id", "branch_code", "branch_name", "branch_type", "address", "city", "state", "country",
        "geographic_zone", "phone", "opening_time", "closing_time", "has_atms", "has_teller_windows",
        "branch_opening_date", "branch_status",
    ],
    "service_agents": [
        "agent_id", "employee_code", "first_name", "last_name", "email", "native_accent", "country_of_origin",
        "agent_type", "experience_level", "languages", "hire_date", "agent_status", "work_shift",
    ],
    "marketing_campaigns": [
        "campaign_id", "campaign_name", "campaign_type", "campaign_objective", "start_date", "end_date",
        "campaign_status",
    ],
    "transactions": [
        "transaction_id", "transaction_date", "process_date", "product_id", "customer_id", "transaction_type",
        "amount", "currency", "channel", "transaction_country", "transaction_status", "is_fraud",
    ],
    "call_center_interactions": [
        "interaction_id", "interaction_date", "process_date", "customer_id", "interaction_type", "channel",
        "contact_reason", "reason_category", "requires_followup", "was_escalated", "has_transcript",
        "has_recording",
    ],
    "call_transcripts": [
        "transcript_id", "interaction_id", "process_date", "customer_id", "agent_id", "full_text",
        "detected_language", "transcription_model", "duration_seconds",
    ],
    "satisfaction_surveys": [
        "survey_id", "survey_date", "process_date", "customer_id", "survey_type", "send_channel", "main_score",
    ],
    "digital_events": [
        "event_id", "event_date", "process_date", "session_id", "event_type", "event_category", "channel",
        "is_mobile",
    ],
    "complaints": [
        "complaint_id", "creation_date", "process_date", "customer_id", "case_type", "category",
        "reception_channel", "description", "priority", "status", "sla_breached", "is_repeat_complainer",
    ],
    "campaign_sends": [
        "send_id", "send_date", "process_date", "campaign_id", "customer_id", "send_channel", "send_status",
        "was_delivered", "had_conversion",
    ],
    "daily_exchange_rates": [
        "date", "source_currency", "target_currency", "exchange_rate",
    ],
}

# UNIQUE columns besides the primary key, from the dictionary.
UNIQUE = {
    "customers": ["document_number"],
    "products": ["product_number"],
    "branches": ["branch_code"],
    "service_agents": ["employee_code"],
}

COUNTRIES = ["México", "Colombia", "Argentina"]
SEGMENTS = ["Premium", "Plus", "Basic", "Student"]
# The data names product types in Spanish; the dictionary lists them in English.
PRODUCT_TYPES = [
    "Cuenta Ahorro", "Cuenta Corriente", "Tarjeta Crédito", "Tarjeta Débito", "Préstamo Personal",
    "Préstamo Hipotecario", "Inversión", "Seguro",
]

# Allowed values: the dictionary's lists, as the data spells them. A value the data has but the
# dictionary doesn't is noted where it's accepted.
ALLOWED_VALUES = {
    "customers": {"segment": SEGMENTS, "customer_status": ["Active", "Inactive", "Suspended", "Closed"],
                  "country": COUNTRIES},
    "products": {"product_type": PRODUCT_TYPES, "currency": CURRENCIES,
                 "product_status": ["Active", "Blocked", "Closed", "Suspended"],
                 "opening_channel": ["Branch", "Web", "App", "Call Center"]},
    "branches": {"country": COUNTRIES, "branch_status": ["Active", "Temporarily Closed", "Closed"]},
    "service_agents": {"country_of_origin": COUNTRIES, "agent_status": ["Active", "Vacation", "Leave", "Inactive"]},
    "marketing_campaigns": {"campaign_type": ["Email", "SMS", "Push", "WhatsApp", "Voice", "Mix"],
                            "campaign_status": ["Planned", "Active", "Paused", "Completed"],
                            "target_segment": SEGMENTS, "target_country": COUNTRIES},
    "campaign_sends": {"send_channel": ["Email", "SMS", "Push", "WhatsApp", "Voice"],
                       "send_status": ["Sent", "Failed", "Bounced", "Blocked"], "open_country": COUNTRIES},
    "transactions": {"currency": CURRENCIES, "channel": ["ATM", "Branch", "Web", "App", "POS", "Transfer"],
                     "transaction_status": ["Approved", "Declined", "Pending", "Reversed"],
                     "transaction_country": [*COUNTRIES, "USA", "Spain", "Brazil"]},
    # "Web" is in the data (2026-10-04) but not in the dictionary's channel list.
    "call_center_interactions": {"channel": ["Phone", "Web Chat", "WhatsApp", "Email", "App", "Web"]},
    "satisfaction_surveys": {"survey_type": ["CSAT", "NPS", "CES"],
                             "send_channel": ["Email", "SMS", "IVR", "App", "Web"]},
    "digital_events": {"channel": ["Android App", "iOS App", "Desktop Web", "Mobile Web"], "ip_country": COUNTRIES},
    "complaints": {"case_type": ["Complaint", "Claim", "Request", "Suggestion"],
                   "status": ["Open", "In Process", "Escalated", "Resolved", "Closed", "Rejected"],
                   "reception_channel": ["Call Center", "Email", "Web", "App", "Branch", "Regulator"],
                   "currency": CURRENCIES},
    "daily_exchange_rates": {"source_currency": CURRENCIES, "target_currency": CURRENCIES},
}

# (min, max) inclusive; max None means no upper bound. Amounts, counts and durations can't be negative.
RANGES = {
    "customers": {"credit_score": (300, 850), "estimated_monthly_income": (0, None)},
    "products": {"current_balance": (0, None), "credit_limit": (0, None), "interest_rate": (0, 100),
                 "days_past_due": (0, None)},
    "branches": {"atm_count": (0, None), "teller_window_count": (0, None)},
    "service_agents": {"avg_csat": (1, 5), "total_monthly_interactions": (0, None)},
    "marketing_campaigns": {"budget": (0, None), "expected_conversion_rate": (0, 100)},
    "campaign_sends": {"click_count": (0, None), "conversion_value": (0, None), "send_cost": (0, None)},
    "transactions": {"amount": (0, None), "amount_usd": (0, None), "fraud_score": (0, 100)},
    "call_center_interactions": {"duration_seconds": (0, None), "wait_time_seconds": (0, None),
                                 "sentiment_score": (-1, 1)},
    "call_transcripts": {"duration_seconds": (0, None), "accent_confidence": (0, 1)},
    "satisfaction_surveys": {"question_1_response": (1, 5), "question_2_response": (1, 5),
                             "question_3_response": (1, 5), "response_time_hours": (0, None),
                             "campaign_response_rate": (0, 100)},
    "digital_events": {"event_value": (0, None), "duration_seconds": (0, None)},
    "complaints": {"claimed_amount": (0, None), "compensation_granted": (0, None), "resolution_days": (0, None),
                   "resolution_satisfaction": (1, 5)},
    "daily_exchange_rates": {"exchange_rate": (0, None), "buy_rate": (0, None), "sell_rate": (0, None)},
}

# The date each event happened on, which must fall inside the dataset's range.
EVENT_DATES = {
    "transactions": "transaction_date",
    "call_center_interactions": "interaction_date",
    "satisfaction_surveys": "survey_date",
    "digital_events": "event_date",
    "complaints": "creation_date",
    "campaign_sends": "send_date",
    "daily_exchange_rates": "date",
}

# (earlier, later) column pairs.
DATE_ORDER = {
    "products": [("opening_date", "expiration_date")],
    "marketing_campaigns": [("start_date", "end_date")],
}

# ---- Late arrivals and volumes: warnings in the job log and reports, never a failed run ----

# A record processed more than this many days after its event arrived late (is_late_arrival).
LATE_ARRIVAL_DAYS = 1

# process_date is the source's business day, which starts in the morning: an event before this time
# belongs to the previous day. Measured on 2026-10-04: every event before the time had the previous
# day's process_date and every one after had its own; events at exactly the time fall on either day.
BUSINESS_DAY_START = {
    "transactions": "06:00:00",
    "campaign_sends": "06:00:00",
    "digital_events": "06:00:00",
    "call_center_interactions": "08:00:00",
    "complaints": "08:00:00",
}
# Tables whose process_date follows another row's time, not their own event's:
#   digital_events: the session's first event (99.999% of rows; the rest start at exactly 06:00:00).
#   call_transcripts, satisfaction_surveys: the interaction they belong to, on its business day.
#   (Measured on 2026-10-04: all transcripts, and 212,757 of 212,759 surveys, which are answered up to
#   36 hours later; the other 2 follow an interaction at exactly 08:00:00, which is either day.)
PROCESS_DATE_FOLLOWS = {
    "digital_events": "session",
    "call_transcripts": "interaction",
    "satisfaction_surveys": "interaction",
}

# Rows per table in the data dictionary, which calls them approximate ("~19,000,000" in total).
# Its 3,000 for daily_exchange_rates can't be right: every pair, every day is 1,097 x 12 = 13,164.
EXPECTED_ROWS = {
    "customers": 150_000,
    "products": 400_000,
    "branches": 350,
    "service_agents": 1_200,
    "marketing_campaigns": 200,
    "transactions": 5_000_000,
    "call_center_interactions": 800_000,
    "call_transcripts": 200_000,
    "satisfaction_surveys": 250_000,
    "digital_events": 10_000_000,
    "complaints": 80_000,
    "campaign_sends": 2_000_000,
    "daily_exchange_rates": 13_164,
}
VOLUME_TOLERANCE_PCT = 20.0  # raw rows further than this from EXPECTED_ROWS are reported
# A day with fewer than LOW x or more than HIGH x the table's median rows for that weekday is reported.
DAILY_VOLUME_LOW, DAILY_VOLUME_HIGH = 0.5, 2.0
