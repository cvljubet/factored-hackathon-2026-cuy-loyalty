import random
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data" / "pipelines" / "serving"))

import load_customer_serving as loader  # noqa: E402
from load_customer_serving import TABLES, MalformedRowError, build_item  # noqa: E402

TS = datetime(2026, 6, 17, 10, 3, 4)


def product(**overrides):
    return {
        "product_type": "Tarjeta Crédito", "product_number_masked": "****4444", "currency": "MXN",
        "current_balance": Decimal("5731.15000000"), "credit_limit": 20000.0, "interest_rate": None,
        "opening_date": date(2023, 11, 5), "expires": "11/2028", "product_status": "Active",
        "opening_channel": "App", "has_linked_app": True, "last_transaction_date": TS,
        "bk_product_id": "PRD-1", "bk_is_overdue": False, **overrides,
    }


def profile_row(**overrides):
    return {
        "customer_id": "CLI-1", "first_name": "Ana", "last_name": "Ruiz", "city": "Puebla", "state": "Puebla",
        "country": "México", "customer_status": "Active", "registration_date": TS, "accepts_marketing": True,
        "products": [product(), product(product_type="Cuenta Ahorro", product_number_masked="****1111")],
        "as_of_date": date(2026, 6, 18), "txn_count_90d": 12, "txn_amount_usd_90d": 310.5,
        "spend_usd_food": 100.25, "agent_name": "Luis G.", "agent_email": "luis@banco.com",
        "email_masked": "a***@mail.com", "mobile_phone_masked": "****2087", "bk_segment": "Premium",
        "bk_agent_id": "AGT-1", "credit_score": 700, **overrides,
    }


# ---- Keys ----


@pytest.mark.parametrize(
    ("table", "row", "sk"),
    [
        ("customer_360", {}, "PROFILE"),
        ("transactions_recent", {"transaction_date": TS, "bk_transaction_id": "TRX-9"}, "TXN#2026-06-17T10:03:04Z#TRX-9"),
        ("contacts_recent", {"interaction_date": TS, "bk_interaction_id": "INT-3"}, "CONTACT#2026-06-17T10:03:04Z#INT-3"),
        ("complaints", {"creation_date": TS, "complaint_id": "QJA-7"}, "COMPLAINT#2026-06-17T10:03:04Z#QJA-7"),
        ("campaigns", {"send_date": TS, "bk_send_id": "SND-5"}, "CAMPAIGN#2026-06-17T10:03:04Z#SND-5"),
    ],
)
def test_customer_keys(table, row, sk):
    assert TABLES[table].keys({"customer_id": "CLI-00BPQUST6X8L", **row}) == ("CUST#CLI-00BPQUST6X8L", sk)


def test_timestamps_are_fixed_width_utc_to_the_second():
    assert loader.iso_timestamp(datetime(2026, 1, 2, 3, 4, 5, 999999)) == "2026-01-02T03:04:05Z"
    lima = timezone(timedelta(hours=-5))
    assert loader.iso_timestamp(datetime(2026, 1, 2, 22, 0, tzinfo=lima)) == "2026-01-03T03:00:00Z"
    assert loader.key_timestamp(date(2026, 1, 2), "d") == "2026-01-02T00:00:00Z"
    early, late = (loader.iso_timestamp(datetime(2026, m, 1, h)) for m, h in [(2, 9), (11, 23)])
    assert len(early) == len(late) == 20 and early < late


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"transaction_date": TS, "bk_transaction_id": "TRX-9"}, "missing customer_id"),
        ({"customer_id": "  ", "transaction_date": TS, "bk_transaction_id": "TRX-9"}, "missing customer_id"),
        ({"customer_id": "CLI-1", "bk_transaction_id": "TRX-9"}, "missing transaction_date"),
        ({"customer_id": "CLI-1", "transaction_date": "yesterday", "bk_transaction_id": "T"}, "not a timestamp"),
        ({"customer_id": "CLI-1", "transaction_date": TS}, "missing bk_transaction_id"),
        ({"customer_id": "CLI#1", "transaction_date": TS, "bk_transaction_id": "TRX-9"}, "key separator"),
    ],
)
def test_malformed_keys_are_refused(row, message):
    with pytest.raises(MalformedRowError, match=message):
        TABLES["transactions_recent"].keys(row)


def test_branch_keys_use_a_normalized_city():
    row = {"city": "Ciudad de México", "bk_branch_id": "SUC-98QOW3F1"}
    assert TABLES["branches"].keys(row) == ("REF#BRANCH", "ciudad_de_mexico#SUC-98QOW3F1")
    assert loader.city_key("  Bogotá ") == "bogota"
    assert loader.city_key("Medellín") == loader.city_key("MEDELLIN")
    with pytest.raises(MalformedRowError, match="missing city"):
        TABLES["branches"].keys({"bk_branch_id": "SUC-1"})


def test_fx_keys():
    assert TABLES["fx_latest"].keys({"source_currency": "USD", "target_currency": "MXN"}) == ("REF#FX", "USD#MXN")
    with pytest.raises(MalformedRowError, match="ISO 4217"):
        TABLES["fx_latest"].keys({"source_currency": "usd", "target_currency": "MXN"})
    with pytest.raises(MalformedRowError, match="missing target_currency"):
        TABLES["fx_latest"].keys({"source_currency": "USD"})


# ---- Fields ----


def test_only_allowed_fields_are_copied():
    item = build_item(TABLES["customer_360"], profile_row(unknown_new_column="x"))
    assert set(item) == {
        "PK", "SK", "first_name", "last_name", "city", "state", "country", "customer_status", "registration_date",
        "accepts_marketing", "products", "as_of_date", "txn_count_90d", "txn_amount_usd_90d", "spend_usd_food",
        "agent_name",
    }
    assert set(item["products"][0]) == set(TABLES["customer_360"].nested["products"]) - {"interest_rate"}


def test_customer_id_lives_in_the_key_only():
    for name, spec in TABLES.items():
        if spec.scope == "customer":
            assert "customer_id" not in spec.fields, name


@pytest.mark.parametrize("spec", [s for s in TABLES.values() if s.scope == "customer"], ids=lambda s: s.name)
def test_forbidden_fields_never_reach_a_customer_item(spec):
    row = {name: "x" for name in loader.CUSTOMER_FORBIDDEN} | {name: "x" for name in spec.excluded}
    row |= {"customer_id": "CLI-1", **{c: TS for c in spec.key_columns if c.endswith("_date")}}
    row |= {c: "ID-1" for c in spec.key_columns if c.endswith("_id") and c != "customer_id"}
    row |= {"valid_on_as_of": True, "products": [product(days_past_due=30, product_number="4111222233334444")]}
    item = build_item(spec, row)
    names = set(item) | {k for v in item.values() if isinstance(v, list) for e in v for k in e}
    assert not names & loader.CUSTOMER_FORBIDDEN
    assert not names & set(spec.excluded)


def test_backend_fields_are_only_the_needed_ones_and_keep_their_prefix():
    for spec in TABLES.values():
        written = set(spec.fields) | {f for fields in spec.nested.values() for f in fields}
        assert {f for f in written if f.startswith("bk_")} <= loader.BACKEND_FIELDS, spec.name
    assert {"bk_mid_rate", "bk_latitude", "bk_had_conversion", "bk_target_segment"}.isdisjoint(
        {f for s in TABLES.values() for f in s.fields}
    )


def test_a_spec_with_a_forbidden_field_is_refused():
    bad = loader.TableSpec("x", "customer", loader.profile_keys, ("customer_id",), ("first_name", "credit_score"))
    stray = loader.TableSpec("y", "reference", loader.fx_keys, (), ("bk_mid_rate",))
    for spec in (bad, stray):
        with pytest.raises(ValueError, match="would write"):
            loader.check_specs([spec])


def test_public_reference_fields_are_kept():
    branch = {"city": "Puebla", "bk_branch_id": "SUC-1", "address": "Av. 5", "email": "sucursal@banco.com",
              "bk_latitude": 19.0, "branch_status": "Active"}
    item = build_item(TABLES["branches"], branch)
    assert item["address"] == "Av. 5" and item["email"] == "sucursal@banco.com" and "bk_latitude" not in item
    fx = build_item(TABLES["fx_latest"], {"date": date(2026, 6, 17), "source_currency": "USD", "target_currency": "MXN",
                                          "buy_rate": 16.81, "sell_rate": 17.23, "bk_mid_rate": 17.02})
    assert fx == {"PK": "REF#FX", "SK": "USD#MXN", "date": "2026-06-17", "source_currency": "USD",
                  "target_currency": "MXN", "buy_rate": Decimal("16.81"), "sell_rate": Decimal("17.23")}


# ---- Campaigns ----


def test_only_campaigns_valid_on_the_as_of_date_are_served():
    spec = TABLES["campaigns"]
    rows = [{"customer_id": "CLI-1", "send_date": TS, "bk_send_id": f"S{i}", "valid_on_as_of": v}
            for i, v in enumerate([True, False, None])]
    stats = loader.publish(spec, [rows])
    assert (stats.rows_read, stats.rows_served) == (3, 1)
    assert "valid_on_as_of" not in stats.sample


# ---- Serialization ----


def test_values_serialize_for_dynamodb():
    assert loader.to_dynamo(0.1) == Decimal("0.1")
    assert loader.to_dynamo(float("nan")) is None
    assert loader.to_dynamo(float("inf")) is None
    assert loader.to_dynamo(Decimal("5731.15000000")) == Decimal("5731.15")
    assert loader.to_dynamo(7) == 7 and loader.to_dynamo(True) is True
    assert loader.to_dynamo(datetime(2026, 6, 17, 1, 2, 3, tzinfo=UTC)) == "2026-06-17T01:02:03Z"
    with pytest.raises(TypeError):
        loader.to_dynamo(b"raw")


def test_nested_products_serialize_without_nulls_or_dropped_fields():
    item = build_item(TABLES["customer_360"], profile_row(products=[product()]))
    assert item["products"] == [{
        "product_type": "Tarjeta Crédito", "product_number_masked": "****4444", "currency": "MXN",
        "current_balance": Decimal("5731.15"), "credit_limit": Decimal("20000.0"), "opening_date": "2023-11-05",
        "expires": "11/2028", "product_status": "Active", "opening_channel": "App", "has_linked_app": True,
        "last_transaction_date": "2026-06-17T10:03:04Z", "bk_is_overdue": False,
    }]
    assert build_item(TABLES["customer_360"], profile_row(products=None)).get("products") is None
    assert build_item(TABLES["customer_360"], profile_row(products=[]))["products"] == []
    with pytest.raises(MalformedRowError, match="products"):
        build_item(TABLES["customer_360"], profile_row(products="not a list"))


def test_oversized_items_are_refused(monkeypatch):
    monkeypatch.setattr(loader, "MAX_ITEM_BYTES", 200)
    with pytest.raises(MalformedRowError, match="400 KB"):
        build_item(TABLES["customer_360"], profile_row())


# ---- Idempotency ----


def test_items_are_deterministic_whatever_the_source_order():
    first = build_item(TABLES["customer_360"], profile_row())
    products = [product(product_type=f"P{i}", opening_date=date(2020, 1, i + 1)) for i in range(6)]
    reordered = {k: v for k, v in reversed(list(profile_row(products=products).items()))}
    shuffled = random.Random(1).sample(products, len(products))
    assert build_item(TABLES["customer_360"], profile_row()) == first
    assert build_item(TABLES["customer_360"], reordered) == build_item(
        TABLES["customer_360"], profile_row(products=shuffled)
    )


# ---- Publishing ----


class FakeTable:
    def __init__(self):
        self.items, self.flushed = [], False

    def batch_writer(self, overwrite_by_pkeys):
        assert overwrite_by_pkeys == ["PK", "SK"]
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.flushed = True

    def put_item(self, Item):
        self.items.append(Item)


def txn(customer, i):
    return {"customer_id": customer, "transaction_date": TS, "bk_transaction_id": f"T{i}", "amount": Decimal("1.5"),
            "bk_response_code": "51"}


def test_publish_writes_counts_and_limits_customers():
    table = FakeTable()
    batches = [[txn("CLI-1", 1), txn("CLI-2", 2)], [txn("CLI-1", 3)]]
    stats = loader.publish(TABLES["transactions_recent"], batches, table, customers=frozenset({"CLI-1"}))
    assert (stats.rows_read, stats.rows_served, stats.items_written) == (3, 2, 2)
    assert table.flushed and [i["SK"] for i in table.items] == ["TXN#2026-06-17T10:03:04Z#T1", "TXN#2026-06-17T10:03:04Z#T3"]
    assert table.items[0]["bk_response_code"] == "51"


def test_customer_limit_does_not_filter_reference_tables():
    rows = [{"source_currency": "USD", "target_currency": "MXN"}]
    assert loader.publish(TABLES["fx_latest"], [rows], customers=frozenset({"CLI-1"})).rows_served == 1


def test_dry_run_writes_nothing():
    stats = loader.publish(TABLES["transactions_recent"], [[txn("CLI-1", 1)]], table=None)
    assert (stats.rows_served, stats.items_written) == (1, 0)


def test_malformed_rows_name_the_table_and_row():
    with pytest.raises(MalformedRowError, match=r"transactions_recent, source row 2: missing customer_id"):
        loader.publish(TABLES["transactions_recent"], [[txn("CLI-1", 1), txn(None, 2)]])


# ---- AWS session ----


def test_session_uses_the_named_profile_or_the_default_chain(monkeypatch):
    calls = []
    monkeypatch.setattr(loader.boto3, "Session", lambda **kwargs: calls.append(kwargs))
    loader.make_session("cuy-loyalty", "us-east-2")
    loader.make_session(None, "us-east-2")
    assert calls == [{"profile_name": "cuy-loyalty", "region_name": "us-east-2"}, {"region_name": "us-east-2"}]


class FakeSession:
    def __init__(self, account):
        self.account = account

    def client(self, name):
        assert name == "sts"
        return self

    def get_caller_identity(self):
        return {"Account": self.account}


def test_other_accounts_are_refused():
    loader.check_account(FakeSession(loader.TEAM_ACCOUNT), loader.TEAM_ACCOUNT)
    with pytest.raises(SystemExit, match="not the team account"):
        loader.check_account(FakeSession("111122223333"), loader.TEAM_ACCOUNT)


def test_cli_needs_a_table():
    with pytest.raises(SystemExit):
        loader.parse_args(["--dry-run"])
    args = loader.parse_args(["--table", "branches", "--table", "fx_latest", "--profile", "cuy-loyalty"])
    assert args.table == ["branches", "fx_latest"] and not args.dry_run
