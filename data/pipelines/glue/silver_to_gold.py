"""Silver -> gold, in two zones: what the ML model and analytics read, and what the assistant reads.

ML and analytics (gold_db):
    gold/product_catalog     one row per product_type, with portfolio stats
    gold/customer_features   one row per customer, numeric features for the model
    gold/fx_daily            one row per currency pair and day: mid rate, buy/sell spread
    gold/_exclusions         per run and silver table: rows left out and why

Agent (agent_db), one table per assistant tool; nothing else is readable by the assistant:
    gold/agent/customer_360         one row per customer: masked profile, products, spend, their agent
    gold/agent/campaigns            one row per delivered campaign send
    gold/agent/transactions_recent  each customer's last 20 transactions
    gold/agent/contacts_recent      each customer's last 10 call-center contacts
    gold/agent/complaints           one row per complaint
    gold/agent/branches             one row per branch (public)
    gold/agent/fx_latest            one row per currency pair: buy/sell on the as-of date (public)

gold/recommendations is written later by the ranking model, not by this job.

Locally:  python silver_to_gold.py --lake_bucket /path/to/lake
"""
import sys

from pyspark.sql import DataFrame, Row, SparkSession, Window
from pyspark.sql import functions as F

from dq_rules import DATASET_END

# The data ends 2026-06-17, so windows are anchored on the latest transaction,
# never on today's date. Snapshots (balances, income) convert to USD at the rate on that day.
WINDOW_DAYS = 90
# The data uses the Spanish labels; the dictionary lists English ones.
PRODUCT_REASONS = ["Producto", "Comercial", "Retención", "Product", "Commercial", "Retention"]

# Silver keeps every row and lists what's wrong with it in dq_reasons; this decides which
# reasons keep a row out of gold. Rows whose customer or product doesn't exist go. A broken
# branch, agent, campaign or interaction reference doesn't change what the row says about
# its customer, so those rows stay. Events dated outside the dataset (outside_dataset, e.g. a
# complaint in 2027) go too: they would count in features and shift the 90-day window, and the
# advisor would mention something that hasn't happened. Customers are never left out: every
# output hangs off them, and the advisor must find them after login.
EXCLUDE_WHEN = {
    "products": ["orphan_customers"],
    "transactions": ["orphan_customers", "orphan_products", "outside_dataset"],
    "call_center_interactions": ["orphan_customers", "outside_dataset"],
    "complaints": ["orphan_customers", "outside_dataset"],
    "satisfaction_surveys": ["orphan_customers", "score_out_of_range", "outside_dataset"],
    "campaign_sends": ["orphan_customers", "outside_dataset"],
}
EXCLUSIONS = "table string, rows_in long, rows_excluded long, excluded_by_reason map<string,long>"

# Gold has two zones. gold/ is for the model and analytics. gold/agent/ is all the assistant can
# read, so a column that isn't there can't reach a reply. In agent tables, columns the model may
# quote have plain names, and backend-only ones (ids, routing, segment) start with "bk_", which the
# tools strip before building a tool result.
BACKEND_PREFIX = "bk_"
# Columns about a customer that no agent table may hold, nested fields included. Public tables
# (branches, exchange rates) are exempt: a branch's own email, phone and address are public.
AGENT_FORBIDDEN = [
    "credit_score", "estimated_monthly_income", "estimated_monthly_income_usd", "date_of_birth", "age",
    "document_number", "document_type", "address", "email", "mobile_phone", "product_number", "days_past_due",
    "max_days_past_due", "escalation_rate", "avg_csat",
]


def get_args(argv):
    if "--JOB_NAME" in argv:
        from awsglue.utils import getResolvedOptions

        return getResolvedOptions(argv, ["lake_bucket", "silver_db", "gold_db", "agent_db"])
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--lake_bucket", required=True)
    p.add_argument("--silver_db", default="")
    p.add_argument("--gold_db", default="")
    p.add_argument("--agent_db", default="")
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


def mask_last4(col):
    """'4111222233334444' -> '****4444'; null stays null."""
    return F.when(col.isNotNull(), F.concat(F.lit("****"), F.substring(col, -4, 4)))


def mask_email(col):
    """'maria.lopez@mail.com' -> 'm***@mail.com'; null stays null."""
    return F.regexp_replace(col, r"^(.)[^@]*@", "$1***@")


def latest_n(df: DataFrame, order_col: str, tie_col: str, n: int) -> DataFrame:
    """Each customer's n most recent rows by order_col. Rows at the same time are ordered by
    tie_col (the row's id), so every run picks the same ones."""
    w = Window.partitionBy("customer_id").orderBy(F.col(order_col).desc(), F.col(tie_col).desc())
    return df.withColumn("_rn", F.row_number().over(w)).where(F.col("_rn") <= n).drop("_rn")


def usd_rates_at_end(fx: DataFrame) -> DataFrame:
    """currency -> USD per unit on the dataset's last day (or the latest day before it with a rate)."""
    to_usd_rows = fx.where(
        (F.col("target_currency") == "USD") & (F.col("date") <= F.lit(DATASET_END)) & F.col("exchange_rate").isNotNull()
    )
    rates = to_usd_rows.groupBy(F.col("source_currency").alias("currency")).agg(
        F.max_by("exchange_rate", "date").alias("usd_rate")
    )
    return rates.unionByName(fx.sparkSession.createDataFrame([("USD", 1.0)], "currency string, usd_rate double"))


def with_balance_usd(products: DataFrame, usd_rates: DataFrame) -> DataFrame:
    return (
        products.join(F.broadcast(usd_rates), "currency", "left")
        .withColumn("current_balance_usd", F.col("current_balance").cast("double") * F.col("usd_rate"))
        .drop("usd_rate")
    )


def build_fx_daily(fx: DataFrame) -> DataFrame:
    """Mid rate for reporting conversions; spread_pct is what buying and selling around it costs."""
    return fx.select(
        "date",
        "source_currency",
        "target_currency",
        F.col("exchange_rate").alias("mid_rate"),
        "buy_rate",
        "sell_rate",
        ((F.col("sell_rate") - F.col("buy_rate")) / F.col("exchange_rate")).alias("spread_pct"),
        "exchange_rate_imputed",
        "dq_is_valid",
    )


def build_product_catalog(products: DataFrame) -> DataFrame:
    """Portfolio statistics for dashboards. Not for the agent: an average rate quoted to a customer
    would read as an offer."""
    return products.groupBy("product_type").agg(
        F.count("*").alias("n_products"),
        F.countDistinct("customer_id").alias("n_customers"),
        F.avg((F.col("product_status") == "Active").cast("int")).alias("share_active"),
        F.avg("current_balance_usd").alias("avg_balance_usd"),  # balances come in USD, COP and ARS
        F.avg("interest_rate").alias("avg_interest_rate"),
    )


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


def fx_cost(purchases: DataFrame, fx: DataFrame) -> DataFrame:
    """Exchange margin on purchases in a currency other than the customer's, in USD per customer.

    The bank sells the foreign currency at sell_rate rather than the mid rate, so each purchase
    costs amount x (sell_rate - mid_rate) in the home currency, converted at that day's rate.
    """
    margin = fx.select(
        F.col("date").alias("_fx_date"),
        F.col("source_currency").alias("currency"),
        F.col("target_currency").alias("_home_currency"),
        (F.col("sell_rate") - F.col("exchange_rate")).alias("_margin"),
    )
    home_to_usd = fx.where(F.col("target_currency") == "USD").select(
        F.col("date").alias("_fx_date"),
        F.col("source_currency").alias("_home_currency"),
        F.col("exchange_rate").alias("_home_usd"),
    )
    return (
        purchases.withColumn("_fx_date", F.to_date("transaction_date"))
        .join(F.broadcast(margin), ["_fx_date", "currency", "_home_currency"], "left")
        .join(F.broadcast(home_to_usd), ["_fx_date", "_home_currency"], "left")
        .groupBy("customer_id")
        .agg(
            F.sum(F.col("amount").cast("double") * F.col("_margin") * F.col("_home_usd")).alias(
                f"fx_cost_usd_{WINDOW_DAYS}d"
            )
        )
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

    home = customers.select(
        "customer_id", F.col("country").alias("_home_country"), F.col("income_currency").alias("_home_currency")
    )
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
    foreign_purchases = recent.where(
        (F.col("transaction_type") == "Purchase") & (F.col("currency") != F.col("_home_currency"))
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
        F.sum(F.col("contact_reason").isin(*PRODUCT_REASONS).cast("int")).alias(
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

    income_rate = F.broadcast(usd_rates_at_end(fx))
    base = customers.join(income_rate, F.col("income_currency") == F.col("currency"), "left").select(
        "customer_id",
        "segment",
        "country",
        "credit_score",
        "estimated_monthly_income",
        (F.col("estimated_monthly_income") * F.col("usd_rate")).alias("estimated_monthly_income_usd"),
        "accepts_marketing",
        F.floor(F.datediff(F.lit(as_of), "date_of_birth") / 365.25).alias("age"),
        F.datediff(F.lit(as_of), "registration_date").alias("tenure_days"),
    )
    out = base
    for part in [prod, holdings, spend, spend_by_cat, fx_cost(foreign_purchases, fx), contacts, complaint_counts, csat,
                 campaigns]:
        out = out.join(part, "customer_id", "left")
    count_cols = [
        c for c, t in out.dtypes
        if t in ("int", "bigint", "double") and c.startswith(("n_", "has_", "txn_", "spend_", "foreign_", "fx_"))
    ]
    return out.na.fill(0, subset=count_cols).withColumn("as_of_date", F.to_date(F.lit(as_of)))


# ---- Agent zone: one table per assistant tool. Explicit column lists only, so a column added to
# silver never reaches the agent by accident. Every customer-scoped table keeps customer_id for
# the serving lookup; backend-only columns are renamed with BACKEND_PREFIX. ----


def bk(name: str, source: str | None = None):
    """A backend-only column: the tools strip it before the model sees the result."""
    return F.col(source or name).alias(f"{BACKEND_PREFIX}{name}")


def build_agent_customer_360(customers, products, features, interactions, agents):
    """Profile, products, spend summary and the agent who last served them. Every customer is kept:
    the assistant must find them after login (see EXCLUDE_WHEN)."""
    profile = customers.select(
        "customer_id", "first_name", "last_name",
        mask_email(F.col("email")).alias("email_masked"),
        mask_last4(F.col("mobile_phone")).alias("mobile_phone_masked"),
        "city", "state", "country", "customer_status", "registration_date", "accepts_marketing",
        bk("segment"), bk("registration_branch_id"),
    )  # document_number, address, date_of_birth, credit_score and income stay out
    product_list = products.groupBy("customer_id").agg(
        F.collect_list(
            F.struct(
                "product_type",
                mask_last4(F.col("product_number")).alias("product_number_masked"),
                "currency", "current_balance", "credit_limit", "interest_rate", "opening_date",
                F.date_format("expiration_date", "MM/yyyy").alias("expires"),
                "product_status", "opening_channel", "has_linked_app", "last_transaction_date",
                bk("product_id"),
                # Whether it's overdue routes the conversation; the number of days isn't shown.
                (F.coalesce("days_past_due", F.lit(0)) > 0).alias(f"{BACKEND_PREFIX}is_overdue"),
            )
        ).alias("products")
    )
    # The figures behind "most of your spending is in travel"; scores and risk features stay in gold.
    spend = features.select(
        "customer_id", "as_of_date", f"txn_count_{WINDOW_DAYS}d", f"txn_amount_usd_{WINDOW_DAYS}d",
        f"foreign_txn_count_{WINDOW_DAYS}d", *[c for c in features.columns if c.startswith("spend_usd_")],
    )
    agent_info = agents.select(
        "agent_id",
        F.concat_ws(" ", "first_name", F.concat(F.substring("last_name", 1, 1), F.lit("."))).alias("agent_name"),
        F.col("email").alias("agent_email"),
        F.col("specialty").alias("agent_specialty"),
        F.col("languages").alias("agent_languages"),
        bk("agent_status"),
    )
    # Inner join first: an interaction whose agent doesn't exist gives no agent, not a nameless one.
    served = interactions.select("customer_id", "agent_id", "interaction_date", "interaction_id")
    my_agent = latest_n(
        served.join(agent_info, "agent_id"), "interaction_date", "interaction_id", 1
    ).select(bk("agent_id"), *[c for c in agent_info.columns if c != "agent_id"], "customer_id")
    return (
        profile.join(product_list, "customer_id", "left")
        .join(spend, "customer_id", "left")
        .join(my_agent, "customer_id", "left")
    )


def build_agent_campaigns(sends, campaigns, as_of):
    """Delivered sends with what the campaign offers. Targeting and conversion are backend-only."""
    c = campaigns.select(
        "campaign_id", "campaign_name", "description", "promoted_product", "start_date", "end_date",
        "campaign_status", "campaign_objective", "target_segment", "target_country",
    )
    return (
        sends.where(F.col("was_delivered"))
        .join(c, "campaign_id", "left")  # a send whose campaign doesn't exist is kept, without details
        .select(
            "customer_id", "send_date", "send_channel", "subject", "campaign_name", "description",
            "promoted_product", "start_date", "end_date", "campaign_status",
            F.lit(as_of).cast("date").between(F.col("start_date"), F.col("end_date")).alias("valid_on_as_of"),
            bk("send_id"), bk("campaign_id"), bk("had_conversion"),
            bk("campaign_objective"), bk("target_segment"), bk("target_country"),
        )
    )


def build_agent_transactions_recent(transactions, fx, products, n=20):
    """Each customer's last n transactions, declined ones included ("why was my card declined?")."""
    # Joined on the owner too: a transaction whose product belongs to someone else
    # (product_owner_mismatch) mustn't show that person's card number.
    masked = products.select(
        "product_id", "customer_id", mask_last4(F.col("product_number")).alias("product_number_masked")
    )
    t = to_usd(transactions, fx).join(masked, ["product_id", "customer_id"], "left")
    return latest_n(t, "transaction_date", "transaction_id", n).select(
        "customer_id", "transaction_date", "transaction_type", "transaction_category", "amount", "currency",
        F.col("amount_usd_filled").alias("amount_usd"), "channel", "merchant_name", "merchant_category",
        "transaction_country", "transaction_city", "transaction_status", "product_number_masked",
        bk("transaction_id"),
        bk("response_code"),  # the tool maps it to a friendly reason
    )


def build_agent_contacts_recent(interactions, n=10):
    return latest_n(interactions, "interaction_date", "interaction_id", n).select(
        "customer_id", "interaction_date", "interaction_type", "channel", "contact_reason", "was_resolved",
        "requires_followup", bk("interaction_id"), bk("agent_id"), bk("was_escalated"),
    )


def build_agent_complaints(complaints):
    return complaints.select(
        "customer_id", "complaint_id", "creation_date", "case_type", "category", "subcategory",
        "reception_channel", "description", "claimed_amount", "currency", "status", "first_response_date",
        "resolution_date", "closing_date", "resolution_days", "resolution", "compensation_granted",
        bk("affected_product_id"), bk("assigned_agent_id"), bk("priority"), bk("sla_breached"),
    )


def build_agent_branches(branches):
    """Public branch directory; coordinates are for a backend distance search, not to be read out."""
    return branches.select(
        "branch_name", "branch_type", "address", "city", "state", "country", "postal_code", "phone", "email",
        "opening_time", "closing_time", "has_atms", "atm_count", "has_teller_windows", "teller_window_count",
        "branch_status", bk("branch_id"), bk("latitude"), bk("longitude"),
    )


def build_agent_fx_latest(fx, as_of):
    """Each pair's buy and sell rate on as_of, or the latest day before it with a rate. The mid rate
    is backend-only: customers deal at buy or sell."""
    rates = fx.where((F.col("date") <= F.lit(as_of).cast("date")) & F.col("exchange_rate").isNotNull())
    latest = rates.groupBy("source_currency", "target_currency").agg(
        F.max_by(F.struct("date", "buy_rate", "sell_rate", "exchange_rate"), "date").alias("r")
    )
    return latest.select(
        F.col("r.date").alias("date"), "source_currency", "target_currency",
        F.col("r.buy_rate").alias("buy_rate"), F.col("r.sell_rate").alias("sell_rate"),
        F.col("r.exchange_rate").alias(f"{BACKEND_PREFIX}mid_rate"),
    )


def write_gold(df: DataFrame, root: str, db: str, table: str, zone: str = "") -> None:
    """Writes gold/<zone>/<table>/ and, with a database, registers it there."""
    prefix = f"gold/{zone}/" if zone else "gold/"
    writer = df.write.mode("overwrite").format("parquet").option("path", f"{root}/{prefix}{table}/")
    if db:
        writer.saveAsTable(f"{db}.{table}")
    else:
        writer.save()
    print(f"[gold] {prefix}{table}: written")


def main(argv):
    args = get_args(argv)
    builder = SparkSession.builder.appName("silver_to_gold")
    if args["gold_db"] or args["agent_db"]:
        builder = builder.enableHiveSupport()
    spark = builder.getOrCreate()
    root = lake_root(args["lake_bucket"])
    exclusions = []

    # Each call records the table's exclusions, so every table is read once and passed around.
    def silver(t):
        return usable_rows(spark.read.parquet(f"{root}/silver/{t}/"), t, exclusions)

    fx = silver("daily_exchange_rates").cache()
    customers, products = silver("customers"), with_balance_usd(silver("products"), usd_rates_at_end(fx)).cache()
    transactions, interactions = silver("transactions").cache(), silver("call_center_interactions").cache()
    complaints, sends = silver("complaints"), silver("campaign_sends")
    features = build_customer_features(
        customers, products, transactions, fx, interactions, complaints, silver("satisfaction_surveys"), sends
    ).cache()

    write_gold(build_product_catalog(products), root, args["gold_db"], "product_catalog")
    write_gold(features, root, args["gold_db"], "customer_features")
    write_gold(build_fx_daily(fx), root, args["gold_db"], "fx_daily")

    as_of = features.agg(F.max("as_of_date")).first()[0]  # the latest transaction's day
    agent_tables = {
        "customer_360": build_agent_customer_360(customers, products, features, interactions, silver("service_agents")),
        "campaigns": build_agent_campaigns(sends, silver("marketing_campaigns"), as_of),
        "transactions_recent": build_agent_transactions_recent(transactions, fx, products),
        "contacts_recent": build_agent_contacts_recent(interactions),
        "complaints": build_agent_complaints(complaints),
        "branches": build_agent_branches(silver("branches")),
        "fx_latest": build_agent_fx_latest(fx, as_of),
    }
    for table, df in agent_tables.items():
        write_gold(df, root, args["agent_db"], table, zone="agent")
    spark.createDataFrame(exclusions, EXCLUSIONS).withColumn("run_at", F.current_timestamp()).write.mode(
        "append"
    ).json(f"{root}/gold/_exclusions/")


if __name__ == "__main__":
    main(sys.argv)
