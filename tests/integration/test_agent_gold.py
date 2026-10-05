"""The agent zone (gold/agent/): what the assistant can read. Planted cases: lake_fixture.py.

Run with: see tests/integration/requirements.txt
"""
import re

from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StructType

import silver_to_gold
from silver_to_gold import AGENT_FORBIDDEN, BACKEND_PREFIX

AGENT_TABLES = [
    "customer_360", "campaigns", "transactions_recent", "contacts_recent", "complaints", "branches", "fx_latest",
]
PUBLIC_TABLES = {"branches", "fx_latest"}  # no customer in them; a branch's own email and address are public
# Ids the customer may quote: their own id (the serving key) and their complaint number.
PLAIN_IDS = {"customer_id", "complaint_id"}


def agent(spark, lake, table):
    return spark.read.parquet(f"{lake}/gold/agent/{table}/")


def field_names(schema, prefix=""):
    """Every field name, nested ones included ("products.product_type")."""
    for field in schema.fields:
        yield prefix + field.name
        inner = field.dataType.elementType if isinstance(field.dataType, ArrayType) else field.dataType
        if isinstance(inner, StructType):
            yield from field_names(inner, f"{prefix}{field.name}.")


def test_no_agent_table_holds_a_forbidden_column(spark, lake):
    for table in AGENT_TABLES:
        if table in PUBLIC_TABLES:
            continue
        leaked = [f for f in field_names(agent(spark, lake, table).schema) if f.split(".")[-1] in AGENT_FORBIDDEN]
        assert leaked == [], table


def test_ids_are_backend_only(spark, lake):
    for table in AGENT_TABLES:
        names = [f.split(".")[-1] for f in field_names(agent(spark, lake, table).schema)]
        plain = [n for n in names if n.endswith("_id") and n not in PLAIN_IDS and not n.startswith(BACKEND_PREFIX)]
        assert plain == [], table


def test_masking(spark):
    df = spark.createDataFrame(
        [("maria.lopez@mail.com", "4111222233334444"), ("a@b.co", "3001234567"), (None, None)], "email string, num string"
    )
    masked = df.select(
        silver_to_gold.mask_email(F.col("email")).alias("e"), silver_to_gold.mask_last4(F.col("num")).alias("n")
    ).collect()
    assert [tuple(r) for r in masked] == [("m***@mail.com", "****4444"), ("a***@b.co", "****4567"), (None, None)]


def test_masked_numbers_never_show_more_than_four_characters(spark, lake):
    numbers = [
        p.product_number_masked
        for r in agent(spark, lake, "customer_360").where(F.col("products").isNotNull()).collect()
        for p in r.products
    ] + [r.product_number_masked for r in agent(spark, lake, "transactions_recent").collect()]
    shown = [n for n in numbers if n is not None]
    assert shown and all(re.fullmatch(r"\*{4}.{1,4}", n) for n in shown), shown


def test_a_transaction_on_someone_elses_product_shows_no_number(spark, lake):
    # T2 is C2's, on C1's product P1 (product_owner_mismatch).
    t2 = agent(spark, lake, "transactions_recent").where("bk_transaction_id = 'T2'").first()
    assert t2.customer_id == "C2" and t2.product_number_masked is None


def test_latest_n_keeps_n_per_customer_and_breaks_ties_by_id(spark):
    rows = [("C1", f"X{i:02d}", "2026-06-01") for i in range(25)] + [("C2", "Y1", "2026-05-01")]
    df = spark.createDataFrame(rows, "customer_id string, row_id string, d string")
    kept = silver_to_gold.latest_n(df, "d", "row_id", 20).collect()
    by_customer = {}
    for r in kept:
        by_customer.setdefault(r.customer_id, []).append(r.row_id)
    # All 25 at the same time: the 20 highest ids, every run.
    assert sorted(by_customer["C1"]) == [f"X{i:02d}" for i in range(5, 25)]
    assert by_customer["C2"] == ["Y1"]


def test_recent_tables_stay_within_their_limits(spark, lake):
    for table, limit in [("transactions_recent", 20), ("contacts_recent", 10)]:
        top = agent(spark, lake, table).groupBy("customer_id").count().agg(F.max("count")).first()[0]
        assert top <= limit, table


def test_my_agent_skips_agents_that_dont_exist(spark, lake):
    # C1's latest contacts, I1 (A1) and I4 (A9, missing), are at the same time: A9 can't be "my agent".
    c1 = agent(spark, lake, "customer_360").where("customer_id = 'C1'").first()
    assert (c1.bk_agent_id, c1.agent_name) == ("A1", "Luis G.")


def test_campaigns_are_delivered_sends_with_readable_subjects(spark, lake):
    sends = {r.bk_send_id: r for r in agent(spark, lake, "campaigns").collect()}
    assert sorted(sends) == ["N1", "N2"]  # N3's customer doesn't exist; N4 wasn't delivered
    assert sends["N1"].subject == "¡Oferta especial en Tarjeta Crédito!"
    assert sends["N1"].valid_on_as_of is True
    assert all("nan" not in (r.subject or "") for r in sends.values())


def test_future_complaints_are_left_out(spark, lake):
    ids = sorted(r.complaint_id for r in agent(spark, lake, "complaints").collect())
    assert ids == ["Q1", "Q4"]  # Q3 is dated 2027; Q2's customer doesn't exist


def test_fx_latest_has_one_rate_per_pair_on_the_as_of_date(spark, lake):
    fx = agent(spark, lake, "fx_latest")
    as_of = spark.read.parquet(f"{lake}/gold/customer_features/").agg(F.max("as_of_date")).first()[0]
    assert fx.count() == fx.select("source_currency", "target_currency").distinct().count() == 12
    assert {r.date for r in fx.select("date").distinct().collect()} == {as_of}
