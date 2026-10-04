"""Table-specific silver rules: flags (dq_invalid_<rule>), imputations (<column>_imputed) and
derived columns, each looking at one table only. bronze_to_silver applies them after
deduplication; rules that compare tables live there.

Deployed next to the job scripts and put on their Python path with --extra-py-files.
"""
from functools import reduce

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

from dq_rules import (
    CAMPAIGN_OBJECTIVES,
    CAMPAIGN_PRODUCTS,
    COUNTRY_BOXES,
    CREDIT_PRODUCTS,
    CREDIT_TERMS,
    CURRENCIES,
    DATASET_END,
    DATASET_START,
    DROPPED_COLUMNS,
    FX_TOLERANCE,
    HOME_CURRENCY,
    NPS_CATEGORIES,
    REQUIRED_FILL_PCT,
)

ENTITIES_SCHEMA = "account_numbers int, dates int, amounts int, products string, _corrupt string"


def lookup(mapping: dict, key: Column) -> Column:
    """mapping[key] as a column; null for keys not in the mapping."""
    return F.create_map(*[F.lit(x) for kv in mapping.items() for x in kv])[key]


def impute(df: DataFrame, column: str, value: Column) -> DataFrame:
    """Fill a null column with value, in place, and mark the filled rows in <column>_imputed."""
    filled = F.col(column).isNull() & value.isNotNull()
    return df.withColumn(f"{column}_imputed", filled).withColumn(column, F.coalesce(F.col(column), value))


def bad_location(lat: Column, lon: Column, country: Column) -> Column:
    """Outside the globe, on "null island" (0, 0), or outside the country's bounding box.
    Null when a coordinate is missing; countries without a box are only checked globally."""
    off_globe = (F.abs(lat) > 90) | (F.abs(lon) > 180)
    null_island = (F.abs(lat) < 1) & (F.abs(lon) < 1)
    outside = F.lit(False)
    for name, (lat_min, lat_max, lon_min, lon_max) in COUNTRY_BOXES.items():
        inside = lat.between(lat_min, lat_max) & lon.between(lon_min, lon_max)
        outside = F.when(country == name, ~inside).otherwise(outside)
    return off_globe | null_island | outside


def branches(df: DataFrame) -> DataFrame:
    return df.withColumn("dq_invalid_location", bad_location(F.col("latitude"), F.col("longitude"), F.col("country")))


def call_center_interactions(df: DataFrame) -> DataFrame:
    # reason_category repeats contact_reason value for value; gold reads contact_reason.
    email = (F.col("interaction_type") == "Email") | (F.col("channel") == "Email")
    missing = F.col("duration_seconds").isNull() & ~F.coalesce(email, F.lit(False))
    return df.drop(*DROPPED_COLUMNS["call_center_interactions"]).withColumn("dq_invalid_missing_duration", missing)


def call_transcripts(df: DataFrame) -> DataFrame:
    """mentioned_entities is JSON such as {"account_numbers": 1, "dates": 0, "amounts": 2,
    "products": "Cuenta Ahorro"}; its fields become entities_* columns. A missing duration_seconds
    (NOT NULL in the dictionary) is flagged by the contract, as required_missing."""
    parsed = F.from_json("mentioned_entities", ENTITIES_SCHEMA, {"columnNameOfCorruptRecord": "_corrupt"})
    df = df.withColumn("_entities", parsed)
    for field in ("account_numbers", "dates", "amounts", "products"):
        df = df.withColumn(f"entities_{field}", F.col(f"_entities.{field}"))
    return df.withColumn("dq_invalid_entities_json", F.col("_entities._corrupt").isNotNull()).drop("_entities")


def campaign_sends(df: DataFrame) -> DataFrame:
    df = impute(df, "was_opened", F.when(F.col("send_status") == "Sent", F.lit(False)))
    engaged = F.col("was_opened") | F.col("was_clicked")
    early = (F.col("open_date") < F.col("send_date")) | (F.col("click_date") < F.col("send_date"))
    return df.withColumn("dq_invalid_undelivered_engagement", ~F.col("was_delivered") & engaged).withColumn(
        "dq_invalid_event_order", early
    )


def customers(df: DataFrame) -> DataFrame:
    end = F.lit(DATASET_END)
    after_end = F.to_date("last_updated") > end
    registration = (F.to_date("registration_date") > end) | (F.col("registration_date") > F.col("last_updated"))
    return (
        df.withColumn("dq_invalid_last_updated_after_cutoff", after_end)
        # The timestamp to trust: deduplication already ignores dates after the cut-off.
        .withColumn("last_updated_clean", F.when(~after_end, F.col("last_updated")))
        .withColumn("dq_invalid_registration_date", registration)
        .withColumn("income_currency", lookup(HOME_CURRENCY, F.col("country")))
    )


def daily_exchange_rates(df: DataFrame) -> DataFrame:
    return fx_consistency(fill_fx_calendar(df))


def fill_fx_calendar(df: DataFrame) -> DataFrame:
    """Every pair, every day of the dataset: a missing day takes the pair's previous day, with
    exchange_rate_imputed (it covers buy_rate, sell_rate and source too). Rows outside the
    calendar or without a key pass through untouched."""
    spark = df.sparkSession
    pairs = [(s, t) for s in CURRENCIES for t in CURRENCIES if s != t]
    days = spark.range(1).select(
        F.explode(F.sequence(F.to_date(F.lit(DATASET_START)), F.to_date(F.lit(DATASET_END)))).alias("date")
    )
    calendar = spark.createDataFrame(pairs, "source_currency string, target_currency string").crossJoin(days)
    key = ["date", "source_currency", "target_currency"]
    keyed = df.where(~F.col("dq_invalid_missing_key")).withColumn("_from_bronze", F.lit(True))
    joined = calendar.join(keyed, key, "full")
    missing = F.col("_from_bronze").isNull()
    previous = Window.partitionBy("source_currency", "target_currency").orderBy("date")
    previous = previous.rowsBetween(Window.unboundedPreceding, 0)
    filled = joined.withColumns(
        {c: F.when(missing, F.last(c, ignorenulls=True).over(previous)).otherwise(F.col(c))
         for c in ("exchange_rate", "buy_rate", "sell_rate", "source")}
    ).withColumns(
        {
            "exchange_rate_imputed": missing,
            "dq_invalid_missing_key": F.coalesce(F.col("dq_invalid_missing_key"), F.lit(False)),
            "dq_copies": F.coalesce(F.col("dq_copies"), F.lit(0)),  # no bronze row behind it
            "dq_versions": F.coalesce(F.col("dq_versions"), F.lit(0)),
        }
    )
    keyless = df.where(F.col("dq_invalid_missing_key")).withColumn("exchange_rate_imputed", F.lit(False))
    return filled.drop("_from_bronze").unionByName(keyless)


def fx_consistency(df: DataFrame) -> DataFrame:
    """buy <= mid <= sell; A->B x B->A close to 1; A->B close to A->C x C->B for every C.
    One wrong rate flags its inverse and the triangles it is part of too."""
    rates = df.where(F.col("exchange_rate").isNotNull()).select(
        "date", F.col("source_currency").alias("_a"), F.col("target_currency").alias("_b"), F.col("exchange_rate")
    )
    inverse = rates.select(
        "date",
        F.col("_b").alias("source_currency"),
        F.col("_a").alias("target_currency"),
        F.col("exchange_rate").alias("_inverse"),
    )
    # A->C joined to C->B, for every C other than A and B.
    left, right = rates.alias("l"), rates.alias("r")
    via = (F.col("l.date") == F.col("r.date")) & (F.col("l._b") == F.col("r._a")) & (F.col("l._a") != F.col("r._b"))
    implied = (
        left.join(right, via)
        .groupBy(
            F.col("l.date").alias("date"),
            F.col("l._a").alias("source_currency"),
            F.col("r._b").alias("target_currency"),
        )
        .agg(
            F.min(F.col("l.exchange_rate") * F.col("r.exchange_rate")).alias("_implied_min"),
            F.max(F.col("l.exchange_rate") * F.col("r.exchange_rate")).alias("_implied_max"),
        )
    )
    key = ["date", "source_currency", "target_currency"]
    rate = F.col("exchange_rate")
    cross_off = F.greatest(F.abs(F.col("_implied_min") / rate - 1), F.abs(F.col("_implied_max") / rate - 1))
    return (
        df.join(inverse, key, "left")
        .join(implied, key, "left")
        .withColumn("dq_invalid_rate_order", ~((F.col("buy_rate") <= rate) & (rate <= F.col("sell_rate"))))
        .withColumn("dq_invalid_inverse_rate", F.abs(rate * F.col("_inverse") - 1) > FX_TOLERANCE)
        .withColumn("dq_invalid_cross_rate", cross_off > FX_TOLERANCE)
        .drop("_inverse", "_implied_min", "_implied_max")
    )


def digital_events(df: DataFrame) -> DataFrame:
    # Mostly pre-login events: fine for funnels, not attributable to a customer.
    return df.withColumn("dq_invalid_missing_customer", F.col("customer_id").isNull())


def marketing_campaigns(df: DataFrame) -> DataFrame:
    parts = F.split("campaign_name", "_")
    df = df.withColumns(
        {
            "objective_code": parts[1],
            "product_code": parts[2],
            "campaign_month": F.to_date(F.concat(F.lit("01"), parts[3]), "ddMMMyyyy"),
            "campaign_seq": parts[4].cast("int"),
        }
    )
    objective = lookup(CAMPAIGN_OBJECTIVES, F.col("objective_code"))
    product = lookup(CAMPAIGN_PRODUCTS, F.col("product_code"))
    known_product = F.col("product_code").isin(*CAMPAIGN_PRODUCTS)
    well_formed = (F.size(parts) == 5) & (parts[0] == "CMP") & objective.isNotNull() & known_product
    month_in_run = F.col("campaign_month").between(F.trunc("start_date", "month"), F.trunc("end_date", "month"))
    contradicts = (
        (F.col("campaign_objective") != objective)
        | (F.col("promoted_product") != product)
        | ~month_in_run
    )
    df = df.withColumns(
        {
            # Unknown codes are flagged, never guessed.
            "dq_invalid_campaign_name": ~(well_formed & F.col("campaign_month").isNotNull()),
            "dq_invalid_name_mismatch": contradicts,  # checked on the source values, before imputing
        }
    )
    df = impute(df, "campaign_objective", objective)
    df = impute(df, "promoted_product", product)
    template = F.concat(
        F.lit("Campaña de "), F.lower("campaign_objective"), F.lit(" para "), F.col("promoted_product")
    )
    return impute(df, "description", template)


def products(df: DataFrame) -> DataFrame:
    """Credit terms and expiration_date are required for a product type only when the data
    nearly always has them for that type (REQUIRED_FILL_PCT)."""
    candidates = {c: CREDIT_PRODUCTS for c in CREDIT_TERMS}
    types = [r[0] for r in df.select("product_type").distinct().collect() if r[0]]
    candidates["expiration_date"] = types
    fill = df.groupBy("product_type").agg(
        *[(100 * F.avg(F.col(c).isNotNull().cast("int"))).alias(c) for c in candidates]
    ).collect()
    required = {
        c: [r["product_type"] for r in fill if r["product_type"] in kinds and r[c] >= REQUIRED_FILL_PCT]
        for c, kinds in candidates.items()
    }
    print(f"[silver] products: required columns per product type {required}")

    def missing(columns):
        return reduce(
            lambda a, b: a | b,
            [F.col("product_type").isin(*required[c]) & F.col(c).isNull() for c in columns if required[c]],
            F.lit(False),
        )

    return df.withColumns(
        {
            "dq_invalid_incomplete_credit_terms": missing(CREDIT_TERMS),
            "dq_invalid_missing_expiration_date": missing(["expiration_date"]),
        }
    )


def satisfaction_surveys(df: DataFrame) -> DataFrame:
    valid_score = (
        F.when(F.col("survey_type") == "CSAT", F.col("main_score").between(1, 5))
        .when(F.col("survey_type") == "NPS", F.col("main_score").between(0, 10))
        .otherwise(F.lit(True))
    )
    expected = None
    for low, high, label in NPS_CATEGORIES:
        in_bin = F.col("main_score").between(low, high)
        expected = F.when(in_bin, label) if expected is None else expected.when(in_bin, label)
    nps = F.col("survey_type") == "NPS"
    wrong = F.when(nps, F.col("nps_category") != expected).otherwise(F.col("nps_category").isNotNull())
    df = df.withColumns(
        {"dq_invalid_score_out_of_range": ~F.coalesce(valid_score, F.lit(False)), "dq_invalid_nps_category": wrong}
    )
    return impute(df, "nps_category", F.when(nps, expected))


def transactions(df: DataFrame) -> DataFrame:
    usd = F.col("currency") == "USD"
    amount = F.col("amount").cast("double")
    # amount_usd is 0 on USD transactions in the source; gold's spend in USD needs the amount.
    replaced = usd & ~F.coalesce(F.col("amount_usd") == amount, F.lit(False))
    return df.withColumns(
        {
            "amount_usd_imputed": replaced,
            "amount_usd": F.when(usd, amount).otherwise(F.col("amount_usd")),
            "dq_invalid_location": bad_location(
                F.col("latitude"), F.col("longitude"), F.col("transaction_country")
            ),
        }
    )


RULES = {
    "branches": branches,
    "call_center_interactions": call_center_interactions,
    "call_transcripts": call_transcripts,
    "campaign_sends": campaign_sends,
    "customers": customers,
    "daily_exchange_rates": daily_exchange_rates,
    "digital_events": digital_events,
    "marketing_campaigns": marketing_campaigns,
    "products": products,
    "satisfaction_surveys": satisfaction_surveys,
    "transactions": transactions,
}


def apply(df: DataFrame, table: str) -> DataFrame:
    return RULES[table](df) if table in RULES else df
