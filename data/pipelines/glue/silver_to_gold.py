"""Silver -> gold: business-ready tables for the advisor agent and the ML model.

    gold/product_catalog     one row per product_type, with portfolio stats and,
                             when present, the team-written benefits KB
    gold/customer_features   one row per customer, numeric features for the model
    gold/customer_360        one row per customer: profile + products + features,
                             what the agent reads after login
    gold/_exclusions         per run and silver table: rows left out and why

gold/recommendations is written later by the ranking model, not by this job.

Locally:  python silver_to_gold.py --lake_bucket /path/to/lake
"""
import sys

from pyspark.sql import DataFrame, Row, SparkSession
from pyspark.sql import functions as F

# The data ends 2026-06-17, so windows are anchored on the latest transaction,
# never on today's date.
WINDOW_DAYS = 90
BENEFITS_KB = "reference/benefits_kb/"  # team-written, synthetic; optional
# The data uses the Spanish labels; the dictionary lists English ones.
PRODUCT_REASONS = ["Producto", "Comercial", "Retención", "Product", "Commercial", "Retention"]

# Silver keeps every row and lists what's wrong with it in dq_reasons; this decides which
# reasons keep a row out of gold. Rows whose customer or product doesn't exist go. A broken
# branch, agent, campaign or interaction reference doesn't change what the row says about
# its customer, so those rows stay. Customers are never left out: every output hangs off
# them, and the advisor must find them after login.
EXCLUDE_WHEN = {
    "products": ["orphan_customers"],
    "transactions": ["orphan_customers", "orphan_products"],
    "call_center_interactions": ["orphan_customers"],
    "complaints": ["orphan_customers"],
    "satisfaction_surveys": ["orphan_customers", "score_out_of_range"],
    "campaign_sends": ["orphan_customers"],
}
EXCLUSIONS = "table string, rows_in long, rows_excluded long, excluded_by_reason map<string,long>"


def get_args(argv):
    if "--JOB_NAME" in argv:
        from awsglue.utils import getResolvedOptions

        return getResolvedOptions(argv, ["lake_bucket", "silver_db", "gold_db"])
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--lake_bucket", required=True)
    p.add_argument("--silver_db", default="")
    p.add_argument("--gold_db", default="")
    return vars(p.parse_known_args(argv[1:])[0])


def lake_root(bucket: str) -> str:
    return bucket.rstrip("/") if bucket.startswith(("/", "file:")) else f"s3://{bucket}"


def usable_rows(df: DataFrame, table: str, exclusions: list) -> DataFrame:
    """Leave out the rows EXCLUDE_WHEN rules out for this table, and record how many per reason."""
    reasons = EXCLUDE_WHEN.get(table)
    if not reasons:
        return df
    excluded = F.exists("dq_reasons", lambda r: r.isin(reasons))
    counts = df.agg(
        F.count("*").alias("rows_in"),
        F.count(F.when(excluded, 1)).alias("rows_excluded"),
        *[F.count(F.when(F.array_contains("dq_reasons", r), 1)).alias(r) for r in reasons],
    ).first()
    by_reason = {r: counts[r] for r in reasons}
    exclusions.append(
        Row(table=table, rows_in=counts["rows_in"], rows_excluded=counts["rows_excluded"], excluded_by_reason=by_reason)
    )
    print(f"[gold] {table}: {counts['rows_excluded']} of {counts['rows_in']} rows left out {by_reason}")
    return df.where(~excluded)


def slug(col):
    """'Tarjeta Crédito' -> 'tarjeta_credito', for pivoted column names."""
    plain = F.translate(F.lower(col), "áéíóúüñ", "aeiouun")
    return F.regexp_replace(plain, r"[^a-z0-9]+", "_")


def build_product_catalog(spark, root, products: DataFrame) -> DataFrame:
    catalog = products.groupBy("product_type").agg(
        F.count("*").alias("n_products"),
        F.countDistinct("customer_id").alias("n_customers"),
        F.avg((F.col("product_status") == "Active").cast("int")).alias("share_active"),
        F.avg("current_balance").alias("avg_balance"),
        F.avg("interest_rate").alias("avg_interest_rate"),
    )
    try:
        kb = spark.read.option("header", True).csv(f"{root}/{BENEFITS_KB}")
    except Exception:  # KB not uploaded yet
        return catalog.withColumn("benefits", F.array().cast("array<struct<benefit_id:string,name:string>>"))
    benefits = kb.groupBy("product_type").agg(
        F.collect_list(F.struct("benefit_id", "name")).alias("benefits")
    )
    return catalog.join(benefits, "product_type", "left")


def to_usd(transactions: DataFrame, fx: DataFrame) -> DataFrame:
    """amount_usd is ~57% missing; fill it from the daily rate table.

    Assumes exchange_rate converts 1 unit of source_currency into target_currency.
    """
    usd = fx.where(F.col("target_currency") == "USD").select(
        F.col("date").alias("_fx_date"), F.col("source_currency").alias("currency"), "exchange_rate"
    )
    t = transactions.withColumn("_fx_date", F.to_date("transaction_date"))
    return (
        t.join(F.broadcast(usd), ["_fx_date", "currency"], "left")
        .withColumn(
            "amount_usd_filled",
            F.coalesce(
                F.col("amount_usd"),
                F.when(F.col("currency") == "USD", F.col("amount")),
                F.col("amount") * F.col("exchange_rate"),
            ),
        )
        .drop("_fx_date", "exchange_rate")
    )


def build_customer_features(customers, products, transactions, fx, interactions, complaints, surveys, sends):
    as_of = transactions.agg(F.max("transaction_date")).first()[0]
    since = F.lit(as_of) - F.expr(f"INTERVAL {WINDOW_DAYS} DAYS")

    prod = products.groupBy("customer_id").agg(
        F.sum((F.col("product_status") == "Active").cast("int")).alias("n_active_products"),
        F.sum(F.coalesce("credit_limit", F.lit(0))).alias("total_credit_limit"),
        F.max(F.coalesce("days_past_due", F.lit(0))).alias("max_days_past_due"),
        F.max(F.col("has_linked_app").cast("int")).alias("has_linked_app"),
    )
    holdings = (
        products.where(F.col("product_status") == "Active")
        .withColumn("flag", F.concat(F.lit("has_"), slug("product_type")))
        .groupBy("customer_id")
        .pivot("flag")
        .agg(F.lit(1))
        .na.fill(0)
    )

    home = customers.select("customer_id", F.col("country").alias("_home_country"))
    recent = (
        to_usd(transactions, fx)
        .where((F.col("transaction_date") > since) & (F.col("transaction_status") == "Approved"))
        .join(home, "customer_id", "left")
    )
    spend = recent.groupBy("customer_id").agg(
        F.count("*").alias(f"txn_count_{WINDOW_DAYS}d"),
        F.sum("amount_usd_filled").alias(f"txn_amount_usd_{WINDOW_DAYS}d"),
        F.countDistinct("transaction_category").alias(f"n_categories_{WINDOW_DAYS}d"),
        F.sum((F.col("transaction_country") != F.col("_home_country")).cast("int")).alias(
            f"foreign_txn_count_{WINDOW_DAYS}d"
        ),
    )
    spend_by_cat = (
        recent.withColumn("cat", F.concat(F.lit("spend_usd_"), slug(F.coalesce("transaction_category", F.lit("other")))))
        .groupBy("customer_id")
        .pivot("cat")
        .agg(F.sum("amount_usd_filled"))
        .na.fill(0)
    )

    contacts = interactions.groupBy("customer_id").agg(
        F.count("*").alias("n_contacts"),
        F.sum(F.col("reason_category").isin(*PRODUCT_REASONS).cast("int")).alias(
            "n_product_contacts"
        ),
        F.avg(F.col("was_escalated").cast("int")).alias("escalation_rate"),
        F.max("interaction_date").alias("last_contact_at"),
    )
    complaint_counts = complaints.groupBy("customer_id").agg(F.count("*").alias("n_complaints"))
    csat = (
        surveys.where(F.col("survey_type") == "CSAT")  # out-of-range scores are left out in usable_rows
        .groupBy("customer_id")
        .agg(F.avg("main_score").alias("avg_csat"))
    )
    # Only pre-send facts: opens and clicks happen after the send and would leak.
    campaigns = sends.groupBy("customer_id").agg(F.count("*").alias("n_campaign_sends"))

    base = customers.select(
        "customer_id",
        "segment",
        "country",
        "credit_score",
        "estimated_monthly_income",
        "accepts_marketing",
        F.floor(F.datediff(F.lit(as_of), "date_of_birth") / 365.25).alias("age"),
        F.datediff(F.lit(as_of), "registration_date").alias("tenure_days"),
    )
    out = base
    for part in [prod, holdings, spend, spend_by_cat, contacts, complaint_counts, csat, campaigns]:
        out = out.join(part, "customer_id", "left")
    count_cols = [
        c for c, t in out.dtypes
        if t in ("int", "bigint", "double") and c.startswith(("n_", "has_", "txn_", "spend_", "foreign_"))
    ]
    return out.na.fill(0, subset=count_cols).withColumn("as_of_date", F.to_date(F.lit(as_of)))


def build_customer_360(customers, products, features):
    product_list = products.groupBy("customer_id").agg(
        F.collect_list(
            F.struct("product_id", "product_type", "product_status", "currency", "current_balance", "credit_limit", "opening_date")
        ).alias("products")
    )
    profile = customers.select(
        "customer_id", "first_name", "last_name", "email", "mobile_phone", "city", "state", "country",
        "segment", "customer_status", "accepts_marketing", "registration_date",
    )  # document_number, address and date_of_birth stay out of what the agent sees
    feats = features.drop("segment", "country", "accepts_marketing")
    return profile.join(product_list, "customer_id", "left").join(feats, "customer_id", "left")


def write_gold(df: DataFrame, root: str, db: str, table: str) -> None:
    writer = df.write.mode("overwrite").format("parquet").option("path", f"{root}/gold/{table}/")
    if db:
        writer.saveAsTable(f"{db}.{table}")
    else:
        writer.save()
    print(f"[gold] {table}: written")


def main(argv):
    args = get_args(argv)
    builder = SparkSession.builder.appName("silver_to_gold")
    if args["gold_db"]:
        builder = builder.enableHiveSupport()
    spark = builder.getOrCreate()
    root = lake_root(args["lake_bucket"])
    exclusions = []

    def silver(t):
        return usable_rows(spark.read.parquet(f"{root}/silver/{t}/"), t, exclusions)

    customers, products = silver("customers"), silver("products")
    features = build_customer_features(
        customers,
        products,
        silver("transactions"),
        silver("daily_exchange_rates"),
        silver("call_center_interactions"),
        silver("complaints"),
        silver("satisfaction_surveys"),
        silver("campaign_sends"),
    ).cache()

    write_gold(build_product_catalog(spark, root, products), root, args["gold_db"], "product_catalog")
    write_gold(features, root, args["gold_db"], "customer_features")
    write_gold(build_customer_360(customers, products, features), root, args["gold_db"], "customer_360")
    spark.createDataFrame(exclusions, EXCLUSIONS).withColumn("run_at", F.current_timestamp()).write.mode(
        "append"
    ).json(f"{root}/gold/_exclusions/")


if __name__ == "__main__":
    main(sys.argv)
