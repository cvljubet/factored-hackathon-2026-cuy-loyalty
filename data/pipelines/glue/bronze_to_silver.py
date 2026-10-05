"""Bronze -> silver: type, clean, deduplicate and quality-flag every raw table.

Reads the raw CSVs under <lake>/bronze/<table>/ingest_date=YYYY-MM-DD/ and writes
Parquet to <lake>/silver/<table>/, registered in the silver Glue database.

Rules that look at one table live in table_rules.py; the ones comparing tables live here.
contracts.py holds the generic rules (NOT NULL, allowed values, ranges, dates, uniqueness) as one
Pandera schema per table: they flag rows here and validate each written table
(silver/_contract_report/).
Late arrivals (process_date well after the event) and volumes (rows against the dictionary, days
without files, unusual days) go to silver/_arrival_report/ and silver/_volume_report/ as warnings;
they never fail the run.
Rows are never dropped for quality. Each broken rule sets a dq_invalid_<rule> flag,
dq_reasons lists the rules a row breaks, and dq_is_valid is true when it breaks none.
Each run appends failing rows per rule to silver/_rule_report/ and orphans per
foreign key to silver/_fk_report/, then fails if any foreign key has more orphans
than dq_rules.ORPHAN_FAIL_PCT. Parents are built before their children, because a
child's foreign keys are checked against the parent's silver table.

On AWS Glue it receives job arguments (--lake_bucket, --silver_db, ...).
Locally it runs against a folder, without a catalog:
    python bronze_to_silver.py --lake_bucket /path/to/lake [--tables customers,products]
With --tables, the parents of the selected tables must already be in silver.
"""
import datetime
import statistics
import sys
from functools import reduce
from graphlib import TopologicalSorter

from pyspark.sql import DataFrame, Row, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

import contracts
import table_rules
from dq_rules import (
    BUSINESS_DAY_START,
    DAILY_VOLUME_HIGH,
    DAILY_VOLUME_LOW,
    DATASET_END,
    DATASET_START,
    EVENT_DATES,
    EXPECTED_ROWS,
    FOREIGN_KEYS,
    FX_MAX_FILLED_DAYS,
    LATE_ARRIVAL_DAYS,
    ORPHAN_FAIL_PCT,
    ORPHAN_WARN_PCT,
    PROCESS_DATE_FOLLOWS,
    VOLUME_TOLERANCE_PCT,
)

# Column types follow the LATAM Bank data dictionary. Columns not listed stay strings.
TABLES = {
    "customers": {
        "pk": ["customer_id"],
        "dates": ["date_of_birth"],
        "timestamps": ["registration_date", "last_updated"],
        "ints": ["credit_score"],
        "doubles": ["estimated_monthly_income"],
        "bools": ["accepts_marketing"],
        "order_by": "last_updated",
    },
    "products": {
        "pk": ["product_id"],
        "dates": ["opening_date", "expiration_date"],
        "timestamps": ["last_transaction_date", "last_updated"],
        "ints": ["days_past_due"],
        "decimals": ["current_balance"],
        "doubles": ["credit_limit", "interest_rate"],
        "bools": ["has_linked_app"],
        "order_by": "last_updated",
    },
    "branches": {
        "pk": ["branch_id"],
        "dates": ["branch_opening_date"],
        "ints": ["atm_count", "teller_window_count"],
        "doubles": ["latitude", "longitude"],
        "bools": ["has_atms", "has_teller_windows"],
    },
    "service_agents": {
        "pk": ["agent_id"],
        "dates": ["hire_date"],
        "ints": ["total_monthly_interactions"],
        "doubles": ["avg_csat"],
    },
    "marketing_campaigns": {
        "pk": ["campaign_id"],
        "dates": ["start_date", "end_date"],
        "decimals": ["budget"],
        "doubles": ["expected_conversion_rate"],
    },
    "campaign_sends": {
        "pk": ["send_id"],
        "timestamps": ["send_date", "open_date", "click_date", "conversion_date"],
        "dates": ["process_date"],
        "ints": ["click_count"],
        "decimals": ["conversion_value", "send_cost"],
        "bools": ["was_delivered", "was_opened", "was_clicked", "had_conversion"],
    },
    "transactions": {
        "pk": ["transaction_id"],
        "timestamps": ["transaction_date"],
        "dates": ["process_date"],
        "decimals": ["amount"],
        "doubles": ["amount_usd", "fraud_score", "latitude", "longitude"],
        "bools": ["is_fraud"],
        "partition_by": "transaction_date",
    },
    "call_center_interactions": {
        "pk": ["interaction_id"],
        "timestamps": ["interaction_date"],
        "dates": ["process_date"],
        "ints": ["duration_seconds", "wait_time_seconds"],
        "doubles": ["sentiment_score"],
        "bools": ["was_resolved", "requires_followup", "was_escalated", "has_transcript", "has_recording"],
        "partition_by": "interaction_date",
    },
    "call_transcripts": {
        "pk": ["transcript_id"],
        "dates": ["process_date"],
        "ints": ["duration_seconds"],
        "doubles": ["accent_confidence"],
    },
    "satisfaction_surveys": {
        "pk": ["survey_id"],
        "timestamps": ["survey_date"],
        "dates": ["process_date"],
        "ints": ["main_score", "question_1_response", "question_2_response", "question_3_response"],
        "doubles": ["response_time_hours", "campaign_response_rate"],
    },
    "digital_events": {
        "pk": ["event_id"],
        "timestamps": ["event_date"],
        "dates": ["process_date"],
        "ints": ["duration_seconds"],
        "doubles": ["event_value"],
        "bools": ["is_mobile"],
        "partition_by": "event_date",
    },
    "complaints": {
        "pk": ["complaint_id"],
        "timestamps": ["creation_date", "assignment_date", "first_response_date", "resolution_date", "closing_date"],
        "dates": ["process_date"],
        "ints": ["resolution_days", "resolution_satisfaction"],
        "decimals": ["claimed_amount", "compensation_granted"],
        "bools": ["sla_breached", "is_repeat_complainer"],
    },
    "daily_exchange_rates": {
        "pk": ["date", "source_currency", "target_currency"],
        "dates": ["date"],
        "doubles": ["exchange_rate", "buy_rate", "sell_rate"],
    },
}

# Expected header of every bronze file: the data dictionary's columns, in its order (the real
# files matched it on 2026-10-04). A file whose header differs fails the job, so a
# schema change in the source is adopted here on purpose, never absorbed silently.
COLUMNS = {
    "customers": [
        "customer_id", "document_number", "document_type", "first_name", "last_name", "date_of_birth", "gender",
        "email", "mobile_phone", "landline_phone", "address", "city", "state", "country", "postal_code",
        "detected_accent", "segment", "credit_score", "estimated_monthly_income", "occupation", "marital_status",
        "education_level", "registration_date", "registration_branch_id", "customer_status", "last_updated",
        "accepts_marketing",
    ],
    "products": [
        "product_id", "customer_id", "product_type", "product_number", "currency", "current_balance",
        "credit_limit", "interest_rate", "opening_date", "expiration_date", "opening_branch_id", "product_status",
        "opening_channel", "has_linked_app", "days_past_due", "last_transaction_date", "last_updated",
    ],
    "branches": [
        "branch_id", "branch_code", "branch_name", "branch_type", "address", "city", "state", "country",
        "postal_code", "geographic_zone", "phone", "email", "opening_time", "closing_time", "has_atms", "atm_count",
        "has_teller_windows", "teller_window_count", "latitude", "longitude", "branch_opening_date",
        "branch_status",
    ],
    "service_agents": [
        "agent_id", "employee_code", "first_name", "last_name", "email", "phone", "native_accent",
        "country_of_origin", "assigned_branch_id", "agent_type", "experience_level", "languages", "specialty",
        "hire_date", "avg_csat", "total_monthly_interactions", "agent_status", "work_shift",
    ],
    "marketing_campaigns": [
        "campaign_id", "campaign_name", "description", "campaign_type", "campaign_objective", "promoted_product",
        "target_segment", "target_country", "start_date", "end_date", "budget", "campaign_status",
        "expected_conversion_rate",
    ],
    "campaign_sends": [
        "send_id", "send_date", "process_date", "campaign_id", "customer_id", "send_channel", "template_used",
        "subject", "send_status", "was_delivered", "was_opened", "open_date", "was_clicked", "click_date",
        "click_count", "had_conversion", "conversion_date", "conversion_value", "open_device", "open_country",
        "failure_reason", "send_cost",
    ],
    "transactions": [
        "transaction_id", "transaction_date", "process_date", "product_id", "customer_id", "transaction_type",
        "transaction_category", "amount", "currency", "amount_usd", "channel", "branch_id", "merchant_name",
        "merchant_category", "transaction_country", "transaction_city", "transaction_status", "response_code",
        "is_fraud", "fraud_score", "latitude", "longitude",
    ],
    "call_center_interactions": [
        "interaction_id", "interaction_date", "process_date", "customer_id", "agent_id", "interaction_type",
        "channel", "contact_reason", "reason_category", "duration_seconds", "wait_time_seconds", "was_resolved",
        "requires_followup", "detected_sentiment", "sentiment_score", "customer_detected_accent",
        "agent_used_accent", "was_escalated", "mentioned_products", "has_transcript", "has_recording",
    ],
    "call_transcripts": [
        "transcript_id", "interaction_id", "process_date", "customer_id", "agent_id", "full_text", "customer_text",
        "agent_text", "detected_language", "detected_accent", "accent_confidence", "detected_keywords",
        "mentioned_entities", "detected_intents", "main_topics", "transcription_model", "audio_quality",
        "duration_seconds",
    ],
    "satisfaction_surveys": [
        "survey_id", "survey_date", "process_date", "interaction_id", "customer_id", "agent_id", "survey_type",
        "send_channel", "main_score", "nps_category", "question_1_text", "question_1_response", "question_2_text",
        "question_2_response", "question_3_text", "question_3_response", "open_comments", "comment_sentiment",
        "response_time_hours", "campaign_response_rate",
    ],
    "digital_events": [
        "event_id", "event_date", "process_date", "customer_id", "session_id", "event_type", "event_category",
        "channel", "platform", "browser", "app_version", "page_url", "page_title", "action", "element_id",
        "product_id", "event_value", "duration_seconds", "ip_address", "ip_country", "ip_city", "is_mobile",
        "referrer", "utm_source", "utm_medium", "utm_campaign",
    ],
    "complaints": [
        "complaint_id", "creation_date", "process_date", "customer_id", "case_type", "category", "subcategory",
        "reception_channel", "affected_product_id", "related_branch_id", "origin_interaction_id", "description",
        "claimed_amount", "currency", "priority", "status", "assigned_agent_id", "assignment_date",
        "first_response_date", "resolution_date", "closing_date", "sla_breached", "resolution_days", "resolution",
        "compensation_granted", "resolution_satisfaction", "is_repeat_complainer",
    ],
    "daily_exchange_rates": [
        "date", "source_currency", "target_currency", "exchange_rate", "buy_rate", "sell_rate", "source",
    ],
}

CONTRACTS = contracts.build_all(TABLES, COLUMNS)

NULL_TOKENS = ["", "nan", "NaN", "None", "null", "NULL", "N/A"]
COUNTRY_FIXES = {"Mexico": "México"}  # transactions mix both spellings
CURRENCY_COLUMNS = ["currency", "source_currency", "target_currency"]
COUNTRY_COLUMNS = ["country", "transaction_country", "target_country", "country_of_origin", "ip_country"]

FLAG_PREFIX = "dq_invalid_"
# rows_removed = exact_duplicates (copies identical to a kept or removed row) + conflicting_duplicates
# (other versions of a key, which lost the tie-break).
DQ_REPORT = (
    "table string, rows_in long, rows_out long, rows_removed long,"
    " duplicate_keys long, exact_duplicates long, conflicting_keys long, conflicting_duplicates long"
)
DQ_REPORT_DUPLICATES = ["duplicate_keys", "exact_duplicates", "conflicting_keys", "conflicting_duplicates"]
RULE_REPORT = "table string, rule string, failing_rows long, rows long, failing_pct double"
ARRIVAL_REPORT = (
    "table string, rows long, rows_with_lag long, late_rows long, late_pct double, processed_before_event long,"
    " lag_p50 int, lag_p90 int, lag_p99 int, lag_max int, same_day long, one_day long, two_to_seven_days long,"
    " eight_to_thirty_days long, over_thirty_days long, status string"
)
VOLUME_REPORT = (
    "table string, raw_rows long, silver_rows long, expected_rows long, vs_expected_pct double, file_days long,"
    " missing_days long, missing_day_list array<string>, unusual_days array<string>, rows_filed_on_other_day long,"
    " status string"
)
# Daily tables keep the source's year=/month=/day= folders: the day a file was delivered for.
FILE_DAY = r"year=(\d{4})/month=(\d{2})/day=(\d{2})"
FK_REPORT = (
    "child_table string, child_column string, parent_table string, parent_column string, nullable boolean,"
    " child_rows long, null_rows long, checked_rows long, orphan_rows long, orphan_pct double, status string,"
    " note string"
)


def get_args(argv):
    names = ["lake_bucket", "silver_db"]
    if "--JOB_NAME" in argv:  # running inside AWS Glue
        from awsglue.utils import getResolvedOptions

        args = getResolvedOptions(argv, names)
        args["tables"] = ""
        if "--tables" in argv:
            args["tables"] = getResolvedOptions(argv, ["tables"])["tables"]
        return args
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--lake_bucket", required=True)
    p.add_argument("--silver_db", default="")
    p.add_argument("--tables", default="")
    return vars(p.parse_known_args(argv[1:])[0])


def lake_root(bucket: str) -> str:
    return bucket.rstrip("/") if bucket.startswith(("/", "file:")) else f"s3://{bucket}"


def read_bronze(spark: SparkSession, root: str, table: str) -> DataFrame:
    # Snapshot tables are one <table>.csv; daily tables keep the source's
    # year=/month=/day= folders. Spark reads both and turns every key=value folder
    # into a column (ingest_date, plus year/month/day for daily tables).
    # Everything comes in as string; types are applied explicitly below so a bad
    # value becomes null instead of failing the job. A bad header, though, fails it:
    # enforceSchema=False checks each file's header against COLUMNS, by name and position.
    schema = StructType([StructField(c, StringType()) for c in COLUMNS[table]])
    df = (
        spark.read.schema(schema)
        .option("header", True)
        .option("enforceSchema", False)
        .option("multiLine", True)  # transcripts contain line breaks
        .option("escape", '"')
        .csv(f"{root}/bronze/{table}/")
        .withColumn("_source_file", F.input_file_name())
    )
    # The event's own date columns (e.g. transaction_date) carry the same information.
    return df.drop(*[c for c in ("year", "month", "day") if c in df.columns])


def read_silver(spark: SparkSession, root: str, table: str) -> DataFrame:
    return spark.read.parquet(f"{root}/silver/{table}/")


def clean(df: DataFrame, cfg: dict) -> DataFrame:
    for c, t in df.dtypes:
        if t == "string" and not c.startswith("_"):
            trimmed = F.trim(F.col(c))
            df = df.withColumn(c, F.when(trimmed.isin(NULL_TOKENS), None).otherwise(trimmed))

    for c in cfg.get("dates", []):
        df = df.withColumn(c, F.to_date(c))
    for c in cfg.get("timestamps", []):
        df = df.withColumn(c, F.to_timestamp(c))
    for c in cfg.get("ints", []):
        df = df.withColumn(c, F.col(c).cast("double").cast("int"))
    for c in cfg.get("decimals", []):  # money: exact, but pandas reads these as Decimal objects
        df = df.withColumn(c, F.col(c).cast("decimal(20,8)"))
    for c in cfg.get("doubles", []):  # non-monetary numerics: float64 downstream
        df = df.withColumn(c, F.col(c).cast("double"))
    for c in cfg.get("bools", []):
        df = df.withColumn(c, F.lower(F.col(c)).isin("true", "1", "t", "yes", "si", "sí"))

    for c in CURRENCY_COLUMNS:
        if c in df.columns:
            df = df.withColumn(c, F.upper(c))
    for c in COUNTRY_COLUMNS:
        if c in df.columns:
            df = df.replace(COUNTRY_FIXES, subset=[c])
    return df


def deduplicate(df: DataFrame, table: str, cfg: dict) -> DataFrame:
    """Keep one row per primary key, always the same one, and say what it replaced.

    The winner is the latest ingest, then the latest update (cfg order_by; a date after the
    dataset ends can't win), then the latest process_date, then the row's content hash and file,
    so reruns pick the same row. dq_copies is how many bronze rows had the key and dq_versions how
    many distinct contents they had (1 = exact copies only). Rows with no key can't be deduplicated
    or joined to; they're kept, one each, flagged dq_invalid_missing_key.
    """
    # Compared after clean(), so "nan" vs "" or "Mexico" vs "México" isn't a conflict.
    df = df.withColumn("_content_hash", F.xxhash64(*COLUMNS[table]))
    order = [F.col("ingest_date").desc()]
    if cfg.get("order_by"):
        updated = F.col(cfg["order_by"])
        order.append(F.when(F.to_date(updated) <= F.lit(DATASET_END), updated).desc_nulls_last())
    if "process_date" in df.columns:
        order.append(F.col("process_date").desc_nulls_last())
    order += [F.col("_content_hash").desc(), F.col("_source_file").desc()]

    missing_key = reduce(lambda a, b: a | b, [F.col(k).isNull() for k in cfg["pk"]])
    key = Window.partitionBy(*cfg["pk"])
    keyed = (
        df.where(~missing_key)
        .withColumn("dq_copies", F.count("*").over(key))
        .withColumn("dq_versions", F.size(F.collect_set("_content_hash").over(key)))
        .withColumn("_rn", F.row_number().over(key.orderBy(*order)))
        .where("_rn = 1")
        .drop("_rn")
    )
    keyless = df.where(missing_key).withColumns({"dq_copies": F.lit(1), "dq_versions": F.lit(1)})
    return (
        keyed.unionByName(keyless)
        .withColumn("dq_invalid_missing_key", missing_key)
        .withColumns({"dq_copies": F.col("dq_copies").cast("int"), "dq_versions": F.col("dq_versions").cast("int")})
        .drop("_content_hash")
    )


def foreign_keys_of(table: str) -> list:
    return [fk for fk in FOREIGN_KEYS if fk.child == table]


def build_order(tables: list) -> list:
    """Parents before children, so every foreign key is checked against a parent built first."""
    parents = {t: {fk.parent for fk in foreign_keys_of(t)} & set(tables) for t in tables}
    return list(TopologicalSorter(parents).static_order())


def add_orphan_flags(spark: SparkSession, root: str, df: DataFrame, table: str) -> DataFrame:
    """dq_invalid_orphan_<parent>: the foreign key matches no row of the parent's silver table.
    A null is an orphan only where the dictionary says NOT NULL. Known-broken keys get no flag."""
    for fk in foreign_keys_of(table):
        if fk.known_broken:
            continue
        # Parent keys are unique: silver is deduplicated on them, so the join can't add rows.
        keys = read_silver(spark, root, fk.parent).select(
            F.col(fk.parent_column).alias("_parent_key"), F.lit(True).alias("_parent_found")
        )
        value = F.col(fk.column)
        orphan = F.when(value.isNull(), F.lit(not fk.nullable)).otherwise(F.col("_parent_found").isNull())
        df = (
            df.join(F.broadcast(keys), value == F.col("_parent_key"), "left")
            .withColumn(f"dq_invalid_orphan_{fk.parent}", orphan)
            .drop("_parent_key", "_parent_found")
        )
    return df


def add_cross_table_flags(spark: SparkSession, root: str, df: DataFrame, table: str, children_built: bool) -> DataFrame:
    """Rows that contradict the row they point to in another table. A missing parent doesn't fire
    these; the orphan flag already covers it. Checks against a child table run only once that
    child is built (children_built)."""
    if table == "products" and children_built:
        latest = (
            read_silver(spark, root, "transactions")
            .groupBy(F.col("product_id").alias("_txn_product"))
            .agg(F.max("transaction_date").alias("last_transaction_date_calc"))
        )
        recorded, actual = F.to_date("last_transaction_date"), F.to_date("last_transaction_date_calc")
        # A product without transactions here may have them outside this extract: not flagged.
        df = (
            df.join(latest, F.col("product_id") == F.col("_txn_product"), "left")
            .drop("_txn_product")
            .withColumn("dq_invalid_last_txn_mismatch", actual.isNotNull() & (recorded.isNull() | (recorded != actual)))
        )
    if table == "transactions":
        products = read_silver(spark, root, "products").select(
            F.col("product_id").alias("_product_id"),
            F.col("customer_id").alias("_product_owner"),
            F.col("opening_date").alias("_product_opened"),
        )
        df = (
            df.join(F.broadcast(products), F.col("product_id") == F.col("_product_id"), "left")
            .withColumn("dq_invalid_product_owner_mismatch", F.col("customer_id") != F.col("_product_owner"))
            .withColumn("dq_invalid_before_product_opening", F.to_date("transaction_date") < F.col("_product_opened"))
            .drop("_product_id", "_product_owner", "_product_opened")
        )
    if table in ("call_transcripts", "satisfaction_surveys"):
        interactions = read_silver(spark, root, "call_center_interactions").select(
            F.col("interaction_id").alias("_interaction_id"), F.col("customer_id").alias("_interaction_customer")
        )
        df = (
            df.join(F.broadcast(interactions), F.col("interaction_id") == F.col("_interaction_id"), "left")
            .withColumn(
                "dq_invalid_interaction_customer_mismatch", F.col("customer_id") != F.col("_interaction_customer")
            )
            .drop("_interaction_id", "_interaction_customer")
        )
    return df


def add_arrival_lag(spark: SparkSession, root: str, df: DataFrame, table: str) -> DataFrame:
    """business_date: the source's business day of the event the row's process_date follows (its
    own, its session's first or its interaction's, see dq_rules.PROCESS_DATE_FOLLOWS), which starts
    at BUSINESS_DAY_START. arrival_lag_days: business_date to process_date; is_late_arrival when
    more than LATE_ARRIVAL_DAYS (late data is still valid: no flag). Processed before its business
    day is impossible: dq_invalid_processed_before_event. Null when the anchor is unknown."""
    if "process_date" not in df.columns or (table not in EVENT_DATES and table not in PROCESS_DATE_FOLLOWS):
        return df
    follows = PROCESS_DATE_FOLLOWS.get(table)
    if follows == "interaction":
        interactions = read_silver(spark, root, "call_center_interactions").select(
            F.col("interaction_id").alias("_anchor_interaction"), F.col("interaction_date").alias("_anchor")
        )
        df = df.join(F.broadcast(interactions), F.col("interaction_id") == F.col("_anchor_interaction"), "left")
        df = df.drop("_anchor_interaction")
        starts_at = BUSINESS_DAY_START["call_center_interactions"]
    elif follows == "session":
        df = df.withColumn("_anchor", F.min(EVENT_DATES[table]).over(Window.partitionBy("session_id")))
        starts_at = BUSINESS_DAY_START[table]
    else:
        df = df.withColumn("_anchor", F.col(EVENT_DATES[table]))
        starts_at = BUSINESS_DAY_START.get(table, "00:00:00")
    day = contracts.business_day(F.col("_anchor"), starts_at)
    h, m, s = map(int, starts_at.split(":"))
    # An event at exactly the start time can be on either day: the previous one is accepted too.
    either_day = (contracts.seconds_of_day(F.col("_anchor")) == h * 3600 + m * 60 + s) & (
        F.col("process_date") == F.date_sub(day, 1)
    )
    business = F.when(either_day, F.date_sub(day, 1)).otherwise(day)
    lag = F.datediff("process_date", "business_date")
    return (
        df.withColumn("business_date", business)
        .withColumns(
            {
                "arrival_lag_days": lag,
                "is_late_arrival": lag > LATE_ARRIVAL_DAYS,
                "dq_invalid_processed_before_event": lag < 0,
            }
        )
        .drop("_anchor")
    )


def add_validity(df: DataFrame) -> DataFrame:
    """dq_reasons: the rules the row breaks (flag names without the prefix); dq_is_valid: none.
    A flag left null, because the rule couldn't be evaluated, counts as not fired."""
    flags = [c for c in df.columns if c.startswith(FLAG_PREFIX)]
    df = df.withColumns({c: F.coalesce(F.col(c), F.lit(False)) for c in flags})
    fired = [F.when(F.col(c), F.lit(c[len(FLAG_PREFIX):])) for c in flags]
    reasons = F.array_compact(F.array(*fired)) if fired else F.array().cast("array<string>")
    return df.withColumn("dq_reasons", reasons).withColumn("dq_is_valid", F.size("dq_reasons") == 0)


def write_silver(df: DataFrame, root: str, db: str, table: str, cfg: dict) -> None:
    # Full rewrite each run: the source is a static snapshot, so no merge logic.
    if cfg.get("partition_by"):
        df = df.withColumn("year_month", F.date_format(cfg["partition_by"], "yyyy-MM"))
    writer = df.write.mode("overwrite").format("parquet").option("path", f"{root}/silver/{table}/")
    if cfg.get("partition_by"):
        writer = writer.partitionBy("year_month")
    if db:
        writer.saveAsTable(f"{db}.{table}")
    else:
        writer.save()


def table_stats(df: DataFrame, table: str) -> dict:
    """Rows, rows failing each rule and nulls in each foreign key of a written table, in one pass."""
    flags = [c for c in df.columns if c.startswith(FLAG_PREFIX)]
    imputed = [c for c in df.columns if c.endswith("_imputed")]
    source_columns = [c for c in COLUMNS[table] if c in df.columns]
    fk_columns = [fk.column for fk in foreign_keys_of(table)]
    return df.agg(
        F.count("*").alias("rows"),
        F.count(F.when(~F.col("dq_is_valid"), 1)).alias("invalid"),
        F.count(F.when(F.col("dq_copies") > 1, 1)).alias("duplicate_keys"),
        F.sum(F.col("dq_copies") - F.col("dq_versions")).alias("exact_duplicates"),
        F.count(F.when(F.col("dq_versions") > 1, 1)).alias("conflicting_keys"),
        F.sum(F.col("dq_versions") - 1).alias("conflicting_duplicates"),
        *[F.count(F.when(F.col(c), 1)).alias(c) for c in flags],
        *[F.count(F.when(F.col(c).isNull(), 1)).alias(f"nulls:{c}") for c in fk_columns],
        *[F.count(F.when(F.col(c), 1)).alias(f"imputed:{c[: -len('_imputed')]}") for c in imputed],
        *[F.count(c).alias(f"filled:{c}") for c in source_columns],
    ).first().asDict()


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 3) if whole else 0.0


def rule_rows(table: str, stats: dict, contract_counts: dict) -> list:
    """One row per flag (<rule>), per imputed column (imputed:<column>), per source column that is
    empty in every row (all_null:<column>, reported once instead of row by row) and per contract
    check (<kind>:<column>)."""
    rows, out = stats["rows"], []
    for name, n in [*stats.items(), *contract_counts.items()]:
        if name.startswith(FLAG_PREFIX):
            rule = name[len(FLAG_PREFIX):]
        elif name.startswith("imputed:") or name.split(":")[0] in contracts.KINDS:
            rule = name
        elif name.startswith("filled:") and n == 0 and rows:
            rule, n = f"all_null:{name[len('filled:'):]}", rows
        else:
            continue
        out.append(Row(table=table, rule=rule, failing_rows=n, rows=rows, failing_pct=pct(n, rows)))
    return out


def unflagged_orphans(spark: SparkSession, root: str, df: DataFrame, table: str) -> dict:
    """Non-null values matching no parent, per known-broken key: they have no flag column to count."""
    counts = {}
    for fk in foreign_keys_of(table):
        if fk.known_broken:
            keys = read_silver(spark, root, fk.parent).select(F.col(fk.parent_column).alias("_parent_key"))
            values = df.where(F.col(fk.column).isNotNull())
            unmatched = values.join(F.broadcast(keys), F.col(fk.column) == F.col("_parent_key"), "left_anti")
            counts[fk.column] = unmatched.count()
    return counts


def fk_rows(table: str, stats: dict, unflagged: dict) -> list:
    rows = []
    for fk in foreign_keys_of(table):
        nulls = stats[f"nulls:{fk.column}"]
        if fk.known_broken:
            orphans = unflagged[fk.column] + (0 if fk.nullable else nulls)
        else:
            orphans = stats[f"dq_invalid_orphan_{fk.parent}"]
        checked = stats["rows"] - nulls if fk.nullable else stats["rows"]
        orphan_pct = pct(orphans, checked)
        if fk.known_broken:
            status = "known_broken"  # reported, never warns or fails; see dq_rules
        elif checked == 0:
            status = "not_checkable"  # every value is null, e.g. complaints.origin_interaction_id
        elif orphan_pct > ORPHAN_FAIL_PCT:
            status = "fail"
        elif orphan_pct > ORPHAN_WARN_PCT:
            status = "warn"
        else:
            status = "ok"
        rows.append(
            Row(
                child_table=table,
                child_column=fk.column,
                parent_table=fk.parent,
                parent_column=fk.parent_column,
                nullable=fk.nullable,
                child_rows=stats["rows"],
                null_rows=nulls,
                checked_rows=checked,
                orphan_rows=orphans,
                orphan_pct=orphan_pct,
                status=status,
                note=fk.known_broken,
            )
        )
    return rows


def write_report(spark: SparkSession, root: str, name: str, rows: list, schema: str) -> None:
    spark.createDataFrame(rows, schema).withColumn("run_at", F.current_timestamp()).write.mode("append").json(
        f"{root}/silver/{name}/"
    )


def arrival_row(df: DataFrame, table: str):
    """The lag distribution of a written daily table, or None for tables without process_date."""
    if "arrival_lag_days" not in df.columns:
        return None
    lag = F.col("arrival_lag_days")
    s = df.agg(
        F.count("*").alias("rows"),
        F.count(lag).alias("rows_with_lag"),
        F.count(F.when(F.col("is_late_arrival"), 1)).alias("late_rows"),
        F.count(F.when(lag < 0, 1)).alias("processed_before_event"),
        F.percentile_approx(lag, [0.5, 0.9, 0.99]).alias("p"),
        F.max(lag).alias("lag_max"),
        F.count(F.when(lag == 0, 1)).alias("same_day"),
        F.count(F.when(lag == 1, 1)).alias("one_day"),
        F.count(F.when(lag.between(2, 7), 1)).alias("two_to_seven_days"),
        F.count(F.when(lag.between(8, 30), 1)).alias("eight_to_thirty_days"),
        F.count(F.when(lag > 30, 1)).alias("over_thirty_days"),
    ).first()
    p50, p90, p99 = s["p"] or [None, None, None]
    late_pct = pct(s["late_rows"], s["rows_with_lag"])
    status = "warn" if s["late_rows"] or s["processed_before_event"] else "ok"
    if status == "warn":
        print(
            f"[arrival] warn: {table}: {s['late_rows']} rows ({late_pct}%) processed more than {LATE_ARRIVAL_DAYS}"
            f" day(s) after the event (p99 {p99} days), {s['processed_before_event']} processed before it"
        )
    return Row(
        table=table, rows=s["rows"], rows_with_lag=s["rows_with_lag"], late_rows=s["late_rows"], late_pct=late_pct,
        processed_before_event=s["processed_before_event"], lag_p50=p50, lag_p90=p90, lag_p99=p99,
        lag_max=s["lag_max"], same_day=s["same_day"], one_day=s["one_day"],
        two_to_seven_days=s["two_to_seven_days"], eight_to_thirty_days=s["eight_to_thirty_days"],
        over_thirty_days=s["over_thirty_days"], status=status,
    )


def unusual_days(rows_per_day: dict) -> list:
    """Days with under DAILY_VOLUME_LOW or over DAILY_VOLUME_HIGH x the median of the same weekday,
    as "day:rows". By weekday because volume has a weekly rhythm: weekends are normally lower."""
    weekday = {day: datetime.date.fromisoformat(day).weekday() for day in rows_per_day}
    medians = {
        w: statistics.median(n for day, n in rows_per_day.items() if weekday[day] == w) for w in set(weekday.values())
    }
    return [
        f"{day}:{n}"
        for day, n in sorted(rows_per_day.items())
        if n < DAILY_VOLUME_LOW * medians[weekday[day]] or n > DAILY_VOLUME_HIGH * medians[weekday[day]]
    ]


def volume_row(raw: DataFrame, table: str, raw_rows: int, silver_rows: int) -> Row:
    """Raw rows against the dictionary and, for daily tables, the days files were delivered for."""
    expected = EXPECTED_ROWS.get(table)
    vs_expected = round(100.0 * (raw_rows / expected - 1), 3) if expected else None
    parts = [F.regexp_extract("_source_file", FILE_DAY, i) for i in (1, 2, 3)]
    file_day = F.to_date(F.concat_ws("-", *parts))
    # raw is uncleaned: process_date is still the source's string.
    elsewhere = F.to_date(F.trim("process_date")) != file_day if "process_date" in raw.columns else F.lit(False)
    days = (
        raw.withColumn("_file_day", file_day)
        .where(F.col("_file_day").isNotNull())
        .groupBy("_file_day")
        .agg(F.count("*").alias("n"), F.count(F.when(elsewhere, 1)).alias("elsewhere"))
        .collect()
    )
    missing, unusual, filed_elsewhere = [], [], None
    if days:  # a daily table
        start, end = datetime.date.fromisoformat(DATASET_START), datetime.date.fromisoformat(DATASET_END)
        delivered = {r["_file_day"] for r in days}
        calendar = (start + datetime.timedelta(days=d) for d in range((end - start).days + 1))
        missing = [str(day) for day in calendar if day not in delivered]
        unusual = unusual_days({str(r["_file_day"]): r["n"] for r in days})
        filed_elsewhere = sum(r["elsewhere"] for r in days)
    off_expected = vs_expected is not None and abs(vs_expected) > VOLUME_TOLERANCE_PCT
    status = "warn" if off_expected or missing or unusual or filed_elsewhere else "ok"
    if off_expected:
        print(f"[volume] warn: {table}: {raw_rows} rows, {vs_expected}% off the dictionary's {expected}")
    if missing:
        print(f"[volume] warn: {table}: no file for {len(missing)} days, e.g. {', '.join(missing[:5])}")
    if unusual:
        print(f"[volume] warn: {table}: {len(unusual)} days with unusual volume, e.g. {', '.join(unusual[:5])}")
    if filed_elsewhere:
        print(f"[volume] warn: {table}: {filed_elsewhere} rows in a file for another day than their process_date")
    return Row(
        table=table, raw_rows=raw_rows, silver_rows=silver_rows, expected_rows=expected,
        vs_expected_pct=vs_expected, file_days=len(days) if days else None, missing_days=len(missing),
        missing_day_list=missing, unusual_days=unusual, rows_filed_on_other_day=filed_elsewhere, status=status,
    )


def check_orphans(fks: list) -> list:
    """Log foreign keys above the warn threshold; return the failure for those above the fail one."""
    for r in fks:
        if r.status in ("warn", "fail", "known_broken"):
            print(f"[fk] {r.status}: {r.child_table}.{r.child_column} -> {r.parent_table}, {r.orphan_pct}% orphans")
    failed = [f"{r.child_table}.{r.child_column} ({r.orphan_pct}%)" for r in fks if r.status == "fail"]
    return [f"Orphans above {ORPHAN_FAIL_PCT}% in {', '.join(failed)}; see silver/_fk_report/"] if failed else []


def check_fx_calendar(df: DataFrame) -> list:
    """The calendar fill is a guard, not a fix: fail if a pair needed more than FX_MAX_FILLED_DAYS
    consecutive filled days, or has days nothing could fill (no earlier rate)."""
    pair = Window.partitionBy("source_currency", "target_currency").orderBy("date")
    last_real = F.last(F.when(~F.col("exchange_rate_imputed"), F.col("date")), ignorenulls=True)
    runs = (
        df.where(F.col("date").between(DATASET_START, DATASET_END) & ~F.col("dq_invalid_missing_key"))
        .withColumn("_filled_run", F.datediff("date", last_real.over(pair.rowsBetween(Window.unboundedPreceding, 0))))
        .groupBy("source_currency", "target_currency")
        .agg(
            F.max("_filled_run").alias("longest_fill"),
            F.count(F.when(F.col("exchange_rate").isNull(), 1)).alias("unfilled"),
        )
        .collect()
    )
    bad = [
        f"{r.source_currency}->{r.target_currency} ({r.longest_fill} days filled in a row, {r.unfilled} unfilled)"
        for r in runs
        if (r.longest_fill or 0) > FX_MAX_FILLED_DAYS or r.unfilled
    ]
    return [f"Exchange rate gaps in {', '.join(bad)}; see silver/daily_exchange_rates"] if bad else []


def main(argv):
    args = get_args(argv)
    builder = SparkSession.builder.appName("bronze_to_silver")
    if args["silver_db"]:
        builder = builder.enableHiveSupport()  # Glue Data Catalog
    spark = builder.getOrCreate()

    root = lake_root(args["lake_bucket"])
    selected = [t for t in args["tables"].split(",") if t] or list(TABLES)
    order = build_order(selected)
    # products' last_transaction_date is checked against transactions, which are built after
    # products because they point to it: when both run, products is built again at the end.
    # (With --tables products alone, transactions must already be in silver.)
    if {"products", "transactions"} <= set(order):
        order.append("products")
    report, rules, fks, contract_findings, arrivals, volumes, failures = [], [], [], [], [], [], []
    for i, table in enumerate(order):
        final = table not in order[i + 1:]
        cfg = TABLES[table]
        raw = read_bronze(spark, root, table).cache()
        silver = table_rules.apply(deduplicate(clean(raw, cfg), table, cfg), table)
        silver = contracts.add_flags(silver, CONTRACTS[table])
        silver = add_cross_table_flags(spark, root, add_orphan_flags(spark, root, silver, table), table, final)
        silver = add_arrival_lag(spark, root, silver, table)
        silver = add_validity(silver).withColumn("_processed_at", F.current_timestamp())
        write_silver(silver, root, args["silver_db"], table, cfg)
        if not final:
            raw.unpersist()
            continue
        written = read_silver(spark, root, table)
        if table == "daily_exchange_rates":
            failures += check_fx_calendar(written)
        stats = table_stats(written, table)
        rules += rule_rows(table, stats, contracts.counts(written, CONTRACTS[table]))
        contract_findings += contracts.validate(written, CONTRACTS[table])
        fks += fk_rows(table, stats, unflagged_orphans(spark, root, written, table))
        rows_in, rows_out = raw.count(), stats["rows"]
        dups = [stats[k] or 0 for k in DQ_REPORT_DUPLICATES]
        report.append((table, rows_in, rows_out, rows_in - rows_out, *dups))
        arrivals += [r for r in [arrival_row(written, table)] if r]
        volumes.append(volume_row(raw, table, rows_in, rows_out))
        raw.unpersist()
        print(
            f"[silver] {table}: {rows_in} raw rows -> {rows_out} rows, {stats['invalid']} not valid;"
            f" removed {dups[1]} exact and {dups[3]} conflicting duplicates"
        )

    write_report(spark, root, "_dq_report", report, DQ_REPORT)
    write_report(spark, root, "_rule_report", rules, RULE_REPORT)
    write_report(spark, root, "_fk_report", fks, FK_REPORT)
    write_report(spark, root, "_contract_report", contract_findings, contracts.CONTRACT_REPORT)
    write_report(spark, root, "_arrival_report", arrivals, ARRIVAL_REPORT)
    write_report(spark, root, "_volume_report", volumes, VOLUME_REPORT)
    failures = check_orphans(fks) + failures
    if failures:
        raise RuntimeError(" | ".join(failures))


if __name__ == "__main__":
    main(sys.argv)
