"""Bronze -> silver: type, clean, deduplicate and quality-flag every raw table.

Reads the raw CSVs under <lake>/bronze/<table>/ingest_date=YYYY-MM-DD/ and writes
Parquet to <lake>/silver/<table>/, registered in the silver Glue database.

On AWS Glue it receives job arguments (--lake_bucket, --silver_db, ...).
Locally it runs against a folder, without a catalog:
    python bronze_to_silver.py --lake_bucket /path/to/lake [--tables customers,products]
"""
import sys

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

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
        df = df.withColumn("dq_score_out_of_range", ~F.coalesce(valid, F.lit(False)))
    if table == "customers":
        df = df.withColumn("dq_credit_score_out_of_range", ~F.col("credit_score").between(300, 850))
    if table == "products":
        # Dictionary lists MXN but the data has none (half the customers are Mexican).
        df = df.withColumn("dq_unexpected_currency", ~F.col("currency").isin("MXN", "COP", "ARS", "USD"))
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


def main(argv):
    args = get_args(argv)
    builder = SparkSession.builder.appName("bronze_to_silver")
    if args["silver_db"]:
        builder = builder.enableHiveSupport()  # Glue Data Catalog
    spark = builder.getOrCreate()

    root = lake_root(args["lake_bucket"])
    selected = [t for t in args["tables"].split(",") if t] or list(TABLES)
    report = []
    for table in selected:
        cfg = TABLES[table]
        raw = read_bronze(spark, root, table).cache()
        silver = deduplicate(add_quality_flags(clean(raw, cfg), table), cfg)
        silver = silver.withColumn("_processed_at", F.current_timestamp())
        write_silver(silver, root, args["silver_db"], table, cfg)
        rows_in, rows_out = raw.count(), spark.read.parquet(f"{root}/silver/{table}/").count()
        report.append((table, rows_in, rows_out, rows_in - rows_out))
        raw.unpersist()
        print(f"[silver] {table}: {rows_in} raw rows -> {rows_out} rows")

    spark.createDataFrame(report, "table string, rows_in long, rows_out long, rows_removed long").withColumn(
        "run_at", F.current_timestamp()
    ).write.mode("append").json(f"{root}/silver/_dq_report/")


if __name__ == "__main__":
    main(sys.argv)
