"""Bronze -> silver: type, clean, deduplicate and quality-flag every raw table.

Reads the raw CSVs under <lake>/bronze/<table>/ingest_date=YYYY-MM-DD/ and writes
Parquet to <lake>/silver/<table>/, registered in the silver Glue database.

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
import sys
from graphlib import TopologicalSorter

from pyspark.sql import DataFrame, Row, SparkSession, Window
from pyspark.sql import functions as F

from dq_rules import FOREIGN_KEYS, ORPHAN_FAIL_PCT, ORPHAN_WARN_PCT

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
        "ints": ["main_score"],
        "doubles": ["response_time_hours"],
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
        "timestamps": ["creation_date", "assignment_date"],
        "dates": ["process_date"],
    },
    "daily_exchange_rates": {
        "pk": ["date", "source_currency", "target_currency"],
        "dates": ["date"],
        "doubles": ["exchange_rate", "buy_rate", "sell_rate"],
    },
}

NULL_TOKENS = ["", "nan", "NaN", "None", "null", "NULL", "N/A"]
COUNTRY_FIXES = {"Mexico": "México"}  # transactions mix both spellings
CURRENCY_COLUMNS = ["currency", "source_currency", "target_currency"]
COUNTRY_COLUMNS = ["country", "transaction_country", "target_country", "country_of_origin", "ip_country"]

FLAG_PREFIX = "dq_invalid_"
RULE_REPORT = "table string, rule string, failing_rows long, rows long, failing_pct double"
FK_REPORT = (
    "child_table string, child_column string, parent_table string, parent_column string, nullable boolean,"
    " child_rows long, null_rows long, checked_rows long, orphan_rows long, orphan_pct double, status string"
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
    # value becomes null instead of failing the job.
    df = (
        spark.read.option("header", True)
        .option("multiLine", True)  # transcripts contain line breaks
        .option("escape", '"')
        .option("inferSchema", False)
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


def add_quality_flags(df: DataFrame, table: str) -> DataFrame:
    """Flag (don't drop) rows that break the data dictionary's rules."""
    if table == "satisfaction_surveys":
        valid = (
            F.when(F.col("survey_type") == "CSAT", F.col("main_score").between(1, 5))
            .when(F.col("survey_type") == "NPS", F.col("main_score").between(0, 10))
            .otherwise(F.lit(True))
        )
        df = df.withColumn("dq_invalid_score_out_of_range", ~F.coalesce(valid, F.lit(False)))
    if table == "customers":
        df = df.withColumn("dq_invalid_credit_score_out_of_range", ~F.col("credit_score").between(300, 850))
    if table == "products":
        # Dictionary lists MXN but the data has none (half the customers are Mexican).
        df = df.withColumn("dq_invalid_currency", ~F.col("currency").isin("MXN", "COP", "ARS", "USD"))
    return df


def deduplicate(df: DataFrame, cfg: dict) -> DataFrame:
    # Latest ingest wins; within one ingest, the most recently updated row wins.
    order = [F.col("ingest_date").desc()]
    if cfg.get("order_by"):
        order.append(F.col(cfg["order_by"]).desc_nulls_last())
    w = Window.partitionBy(*cfg["pk"]).orderBy(*order)
    return (
        df.where(F.concat_ws("", *[F.col(k) for k in cfg["pk"]]) != "")  # drop rows with no key
        .withColumn("_rn", F.row_number().over(w))
        .where("_rn = 1")
        .drop("_rn")
    )


def foreign_keys_of(table: str) -> list:
    return [fk for fk in FOREIGN_KEYS if fk.child == table]


def build_order(tables: list) -> list:
    """Parents before children, so every foreign key is checked against a parent built first."""
    parents = {t: {fk.parent for fk in foreign_keys_of(t)} & set(tables) for t in tables}
    return list(TopologicalSorter(parents).static_order())


def add_orphan_flags(spark: SparkSession, root: str, df: DataFrame, table: str) -> DataFrame:
    """dq_invalid_orphan_<parent>: the foreign key matches no row of the parent's silver table.
    A null is an orphan only where the dictionary says NOT NULL."""
    for fk in foreign_keys_of(table):
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


def add_cross_table_flags(spark: SparkSession, root: str, df: DataFrame, table: str) -> DataFrame:
    """Rows that contradict the parent row they point to. A missing parent doesn't fire these;
    the orphan flag already covers it."""
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
    fk_columns = [fk.column for fk in foreign_keys_of(table)]
    return df.agg(
        F.count("*").alias("rows"),
        F.count(F.when(~F.col("dq_is_valid"), 1)).alias("invalid"),
        *[F.count(F.when(F.col(c), 1)).alias(c) for c in flags],
        *[F.count(F.when(F.col(c).isNull(), 1)).alias(f"nulls:{c}") for c in fk_columns],
    ).first().asDict()


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 3) if whole else 0.0


def rule_rows(table: str, stats: dict) -> list:
    return [
        Row(table=table, rule=c[len(FLAG_PREFIX):], failing_rows=n, rows=stats["rows"], failing_pct=pct(n, stats["rows"]))
        for c, n in stats.items()
        if c.startswith(FLAG_PREFIX)
    ]


def fk_rows(table: str, stats: dict) -> list:
    rows = []
    for fk in foreign_keys_of(table):
        nulls, orphans = stats[f"nulls:{fk.column}"], stats[f"dq_invalid_orphan_{fk.parent}"]
        checked = stats["rows"] - nulls if fk.nullable else stats["rows"]
        orphan_pct = pct(orphans, checked)
        if checked == 0:
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
            )
        )
    return rows


def write_report(spark: SparkSession, root: str, name: str, rows: list, schema: str) -> None:
    spark.createDataFrame(rows, schema).withColumn("run_at", F.current_timestamp()).write.mode("append").json(
        f"{root}/silver/{name}/"
    )


def check_orphans(fks: list) -> None:
    """Log foreign keys above the warn threshold; fail the job if any is above the fail one."""
    for r in fks:
        if r.status in ("warn", "fail"):
            print(f"[fk] {r.status}: {r.child_table}.{r.child_column} -> {r.parent_table}, {r.orphan_pct}% orphans")
    failed = [f"{r.child_table}.{r.child_column} ({r.orphan_pct}%)" for r in fks if r.status == "fail"]
    if failed:
        raise RuntimeError(f"Orphans above {ORPHAN_FAIL_PCT}% in {', '.join(failed)}; see silver/_fk_report/")


def main(argv):
    args = get_args(argv)
    builder = SparkSession.builder.appName("bronze_to_silver")
    if args["silver_db"]:
        builder = builder.enableHiveSupport()  # Glue Data Catalog
    spark = builder.getOrCreate()

    root = lake_root(args["lake_bucket"])
    selected = [t for t in args["tables"].split(",") if t] or list(TABLES)
    report, rules, fks = [], [], []
    for table in build_order(selected):
        cfg = TABLES[table]
        raw = read_bronze(spark, root, table).cache()
        silver = deduplicate(add_quality_flags(clean(raw, cfg), table), cfg)
        silver = add_cross_table_flags(spark, root, add_orphan_flags(spark, root, silver, table), table)
        silver = add_validity(silver).withColumn("_processed_at", F.current_timestamp())
        write_silver(silver, root, args["silver_db"], table, cfg)
        stats = table_stats(read_silver(spark, root, table), table)
        rules += rule_rows(table, stats)
        fks += fk_rows(table, stats)
        rows_in, rows_out = raw.count(), stats["rows"]
        report.append((table, rows_in, rows_out, rows_in - rows_out))
        raw.unpersist()
        print(f"[silver] {table}: {rows_in} raw rows -> {rows_out} rows, {stats['invalid']} not valid")

    write_report(spark, root, "_dq_report", report, "table string, rows_in long, rows_out long, rows_removed long")
    write_report(spark, root, "_rule_report", rules, RULE_REPORT)
    write_report(spark, root, "_fk_report", fks, FK_REPORT)
    check_orphans(fks)


if __name__ == "__main__":
    main(sys.argv)
