"""Agent zone (Glue database cuy_loyalty_dev_agent) -> the DynamoDB customer-serving table.

One item per served row, keyed the way the agent's tools read them:

    customer_360         PK = CUST#<customer_id>  SK = PROFILE
    transactions_recent  PK = CUST#<customer_id>  SK = TXN#<ts>#<transaction_id>
    contacts_recent      PK = CUST#<customer_id>  SK = CONTACT#<ts>#<interaction_id>
    complaints           PK = CUST#<customer_id>  SK = COMPLAINT#<ts>#<complaint_id>
    campaigns            PK = CUST#<customer_id>  SK = CAMPAIGN#<ts>#<send_id>   (valid_on_as_of rows only)
    branches             PK = REF#BRANCH          SK = <city_key>#<branch_id>
    fx_latest            PK = REF#FX              SK = <source_currency>#<target_currency>

<ts> is UTC, YYYY-MM-DDTHH:MM:SSZ, so a customer's items of one kind sort by time. Only the columns
in each table's FIELDS are copied; customer_id lives in PK only. Backend-only fields keep the bk_
prefix, and the tools strip them before the model sees a result. SK = RECO is written by the
ranking model, not here.

Items are rebuilt the same way from the same rows and put over the existing ones, so a rerun is
harmless; rows that left the agent zone since the last run are not deleted.

Locally, against the team account (a profile for another account is refused):
    uv run --no-project --with-requirements data/pipelines/serving/requirements.txt \\
        python data/pipelines/serving/load_customer_serving.py --profile cuy-loyalty --all --dry-run
"""

import argparse
import hashlib
import io
import json
import math
import re
import sys
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import boto3

TEAM_ACCOUNT = "962450756990"
REGION = "us-east-2"
DATABASE = "cuy_loyalty_dev_agent"
SERVING_TABLE = "cuy-loyalty-dev-customer-serving"
BACKEND_PREFIX = "bk_"
# DynamoDB refuses items over 400 KB; the largest customer_360 row is about 5 KB.
MAX_ITEM_BYTES = 400_000

# Backend-only fields that deterministic code needs: the transaction tool turns the response code
# into a friendly decline reason, and the router uses the overdue flag. Any other bk_ column stays out.
BACKEND_FIELDS = frozenset({"bk_response_code", "bk_is_overdue"})

# Never written to a customer item, nested fields included: what AGENT_FORBIDDEN in
# silver_to_gold.py keeps out of the agent zone, the masked contact details (no tool returns them),
# and targeting or internal fields with no online use. Public reference tables are exempt, as in
# silver_to_gold.py: a branch's own address and email are public.
CUSTOMER_FORBIDDEN = frozenset({
    # silver_to_gold.AGENT_FORBIDDEN
    "credit_score", "estimated_monthly_income", "estimated_monthly_income_usd", "date_of_birth", "age",
    "document_number", "document_type", "address", "email", "mobile_phone", "product_number", "days_past_due",
    "max_days_past_due", "escalation_rate", "avg_csat",
    # in PK only
    "customer_id",
    # contact details
    "email_masked", "mobile_phone_masked", "agent_email",
    # targeting, routing and internal ids
    "bk_segment", "bk_registration_branch_id", "bk_agent_id", "bk_agent_status", "bk_product_id",
    "bk_campaign_id", "bk_had_conversion", "bk_campaign_objective", "bk_target_segment", "bk_target_country",
    "bk_was_escalated", "bk_affected_product_id", "bk_assigned_agent_id", "bk_priority", "bk_sla_breached",
})


class MalformedRowError(ValueError):
    """A source row lacks a value its DynamoDB key needs, or has one that can't be used in a key."""


# ---- Keys ----


def key_part(value: Any, what: str) -> str:
    if value is None or not str(value).strip():
        raise MalformedRowError(f"missing {what}")
    text = str(value).strip()
    if "#" in text:
        raise MalformedRowError(f"{what} {text!r} contains '#', the key separator")
    return text


def iso_timestamp(value: datetime) -> str:
    """Fixed-width UTC, to the second: 2026-06-17T10:03:04Z. Naive values are taken as UTC (Glue runs in UTC)."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def key_timestamp(value: Any, what: str) -> str:
    if value is None:
        raise MalformedRowError(f"missing {what}")
    if isinstance(value, datetime):
        return iso_timestamp(value)
    if isinstance(value, date):
        return iso_timestamp(datetime(value.year, value.month, value.day))
    raise MalformedRowError(f"{what} is not a timestamp: {value!r}")


def city_key(city: Any) -> str:
    """'Ciudad de México' -> 'ciudad_de_mexico'. The branch tool must normalize the city it is asked
    for the same way."""
    if city is None:
        raise MalformedRowError("missing city")
    decomposed = unicodedata.normalize("NFKD", str(city).lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    key = re.sub(r"[^a-z0-9]+", "_", plain).strip("_")
    if not key:
        raise MalformedRowError(f"city {city!r} has no letters or digits")
    return key


def currency_code(value: Any, what: str) -> str:
    code = key_part(value, what)
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise MalformedRowError(f"{what} {code!r} is not an ISO 4217 code")
    return code


def customer_pk(row: Mapping[str, Any]) -> str:
    return f"CUST#{key_part(row.get('customer_id'), 'customer_id')}"


def profile_keys(row: Mapping[str, Any]) -> tuple[str, str]:
    return customer_pk(row), "PROFILE"


def event_keys(prefix: str, time_column: str, id_column: str) -> Callable[[Mapping[str, Any]], tuple[str, str]]:
    """Keys for one of a customer's events: CUST#<id>, <prefix>#<ts>#<event id>."""

    def keys(row: Mapping[str, Any]) -> tuple[str, str]:
        ts = key_timestamp(row.get(time_column), time_column)
        return customer_pk(row), f"{prefix}#{ts}#{key_part(row.get(id_column), id_column)}"

    return keys


def branch_keys(row: Mapping[str, Any]) -> tuple[str, str]:
    return "REF#BRANCH", f"{city_key(row.get('city'))}#{key_part(row.get('bk_branch_id'), 'bk_branch_id')}"


def fx_keys(row: Mapping[str, Any]) -> tuple[str, str]:
    source = currency_code(row.get("source_currency"), "source_currency")
    return "REF#FX", f"{source}#{currency_code(row.get('target_currency'), 'target_currency')}"


# ---- What each table serves ----


@dataclass(frozen=True)
class TableSpec:
    name: str
    scope: str  # "customer" (PK = CUST#<id>) or "reference" (public, global)
    keys: Callable[[Mapping[str, Any]], tuple[str, str]]
    key_columns: tuple[str, ...]
    # Copied as item attributes; everything else is left out.
    fields: tuple[str, ...]
    # Source columns left out on purpose; any other unlisted column is reported as unclassified.
    excluded: tuple[str, ...] = ()
    # Struct-list columns: the fields copied from each element.
    nested: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # A boolean column that must be true for the row to be served.
    where: str | None = None

    @property
    def columns(self) -> list[str]:
        """The source columns to read."""
        wanted = [*self.key_columns, *self.fields, *([self.where] if self.where else [])]
        return list(dict.fromkeys(wanted))


TABLES: dict[str, TableSpec] = {
    spec.name: spec
    for spec in [
        TableSpec(
            name="customer_360",
            scope="customer",
            keys=profile_keys,
            key_columns=("customer_id",),
            fields=(
                "first_name", "last_name", "city", "state", "country", "customer_status", "registration_date",
                "accepts_marketing", "products", "as_of_date", "txn_count_90d", "txn_amount_usd_90d",
                "foreign_txn_count_90d", "spend_usd_entertainment", "spend_usd_food", "spend_usd_health",
                "spend_usd_other", "spend_usd_services", "spend_usd_transport",
                "agent_name", "agent_specialty", "agent_languages",
            ),
            excluded=(
                "email_masked", "mobile_phone_masked", "agent_email",
                "bk_segment", "bk_registration_branch_id", "bk_agent_id", "bk_agent_status",
            ),
            # products[].bk_product_id is left out too.
            nested={
                "products": (
                    "product_type", "product_number_masked", "currency", "current_balance", "credit_limit",
                    "interest_rate", "opening_date", "expires", "product_status", "opening_channel",
                    "has_linked_app", "last_transaction_date", "bk_is_overdue",
                ),
            },
        ),
        TableSpec(
            name="transactions_recent",
            scope="customer",
            keys=event_keys("TXN", "transaction_date", "bk_transaction_id"),
            key_columns=("customer_id", "transaction_date", "bk_transaction_id"),
            fields=(
                "transaction_date", "transaction_type", "transaction_category", "amount", "currency", "amount_usd",
                "channel", "merchant_name", "merchant_category", "transaction_country", "transaction_city",
                "transaction_status", "product_number_masked", "bk_response_code",
            ),
        ),
        TableSpec(
            name="contacts_recent",
            scope="customer",
            keys=event_keys("CONTACT", "interaction_date", "bk_interaction_id"),
            key_columns=("customer_id", "interaction_date", "bk_interaction_id"),
            fields=(
                "interaction_date", "interaction_type", "channel", "contact_reason", "was_resolved",
                "requires_followup",
            ),
            excluded=("bk_agent_id", "bk_was_escalated"),
        ),
        TableSpec(
            name="complaints",
            scope="customer",
            keys=event_keys("COMPLAINT", "creation_date", "complaint_id"),
            key_columns=("customer_id", "creation_date", "complaint_id"),
            fields=(
                # complaint_id is the one id the customer may quote (docs/architecture.md).
                "complaint_id", "creation_date", "case_type", "category", "subcategory", "reception_channel",
                "description", "claimed_amount", "currency", "status", "first_response_date", "resolution_date",
                "closing_date", "resolution_days", "resolution", "compensation_granted",
            ),
            excluded=("bk_affected_product_id", "bk_assigned_agent_id", "bk_priority", "bk_sla_breached"),
        ),
        # The agent zone keeps every delivered send since 2023; only the offers running on the
        # as-of date are "enrolled in or eligible for", so only those are served.
        TableSpec(
            name="campaigns",
            scope="customer",
            keys=event_keys("CAMPAIGN", "send_date", "bk_send_id"),
            key_columns=("customer_id", "send_date", "bk_send_id"),
            fields=(
                "send_date", "send_channel", "subject", "campaign_name", "description", "promoted_product",
                "start_date", "end_date", "campaign_status",
            ),
            excluded=(
                "bk_campaign_id", "bk_had_conversion", "bk_campaign_objective", "bk_target_segment",
                "bk_target_country",
            ),
            where="valid_on_as_of",
        ),
        TableSpec(
            name="branches",
            scope="reference",
            keys=branch_keys,
            key_columns=("city", "bk_branch_id"),
            fields=(
                "branch_name", "branch_type", "address", "city", "state", "country", "postal_code", "phone", "email",
                "opening_time", "closing_time", "has_atms", "atm_count", "has_teller_windows",
                "teller_window_count", "branch_status",
            ),
            # No distance search reads the coordinates (and some are wrong).
            excluded=("bk_latitude", "bk_longitude"),
        ),
        TableSpec(
            name="fx_latest",
            scope="reference",
            keys=fx_keys,
            key_columns=("source_currency", "target_currency"),
            fields=("date", "source_currency", "target_currency", "buy_rate", "sell_rate"),
            # Customers deal at buy or sell.
            excluded=("bk_mid_rate",),
        ),
    ]
}


def check_specs(specs: Iterable[TableSpec]) -> None:
    """Fails on a spec that would write a forbidden or unneeded backend field."""
    for spec in specs:
        written = set(spec.fields) | {f for fields in spec.nested.values() for f in fields}
        stray = {f for f in written if f.startswith(BACKEND_PREFIX)} - BACKEND_FIELDS
        forbidden = written & CUSTOMER_FORBIDDEN if spec.scope == "customer" else set()
        if stray or forbidden:
            raise ValueError(f"{spec.name} would write {sorted(stray | forbidden)}")


check_specs(TABLES.values())


# ---- Items ----


def to_dynamo(value: Any) -> Any:
    """A source value as boto3 writes it to DynamoDB: numbers as int or Decimal, dates as ISO text.
    None means "leave the attribute out" (also for NaN and infinities)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return Decimal(repr(value)) if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, datetime):
        return iso_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"no DynamoDB form for {type(value).__name__}")


def nested_value(value: Any, allowed: tuple[str, ...], column: str) -> Any:
    """A struct, or a list of structs, cut to the allowed fields. Lists come out in one fixed order,
    whatever order the source collected them in."""

    def struct(element: Mapping[str, Any]) -> dict[str, Any]:
        return {name: v for name in allowed if (v := to_dynamo(element.get(name))) is not None}

    if value is None:
        return None
    if isinstance(value, Mapping):
        return struct(value)
    if isinstance(value, (list, tuple)):
        elements = [struct(element) for element in value if element is not None]
        return sorted(elements, key=lambda e: json.dumps(e, sort_keys=True, default=str))
    raise MalformedRowError(f"{column} is not a struct or a list of structs")


def build_item(spec: TableSpec, row: Mapping[str, Any]) -> dict[str, Any]:
    """The DynamoDB item for one source row: PK, SK and the allowed fields that have a value."""
    pk, sk = spec.keys(row)
    item: dict[str, Any] = {"PK": pk, "SK": sk}
    for name in spec.fields:
        if name in spec.nested:
            value = nested_value(row.get(name), spec.nested[name], name)
        else:
            value = to_dynamo(row.get(name))
        if value is not None:
            item[name] = value
    size = len(json.dumps(item, default=str).encode())
    if size > MAX_ITEM_BYTES:
        raise MalformedRowError(f"item {pk} {sk} is about {size} bytes, over DynamoDB's 400 KB")
    return item


def shard_of(customer_id: Any, shards: int) -> int:
    """A stable shard for a customer (the same in every process, unlike Python's hash())."""
    digest = hashlib.blake2b(str(customer_id).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % shards


def parse_shard(text: str) -> tuple[int, int]:
    """'3/8' -> (3, 8): this process loads the customers in shard 3 of 8."""
    match = re.fullmatch(r"(\d+)/(\d+)", text)
    if not match or not 0 <= int(match.group(1)) < int(match.group(2)):
        raise argparse.ArgumentTypeError(f"--shard must be K/N with 0 <= K < N, not {text!r}")
    return int(match.group(1)), int(match.group(2))


def is_served(spec: TableSpec, row: Mapping[str, Any], customers: frozenset[str] | None,
              shard: tuple[int, int] | None = None) -> bool:
    if spec.where and row.get(spec.where) is not True:
        return False
    if spec.scope != "customer":
        return True
    if shard is not None and shard_of(row.get("customer_id"), shard[1]) != shard[0]:
        return False
    return customers is None or row.get("customer_id") in customers


# ---- Reading the agent zone ----


class AgentZone:
    """Reads agent-zone tables from the S3 location the Glue Catalog gives, one Parquet file and
    one record batch at a time."""

    def __init__(self, session: boto3.Session, database: str):
        self.glue = session.client("glue")
        self.s3 = session.client("s3")
        self.database = database

    def location(self, table: str) -> tuple[str, str]:
        descriptor = self.glue.get_table(DatabaseName=self.database, Name=table)["Table"]["StorageDescriptor"]
        if "parquet" not in descriptor.get("InputFormat", "").lower():
            raise ValueError(f"{self.database}.{table} is not stored as Parquet")
        match = re.fullmatch(r"s3a?://([^/]+)/?(.*)", descriptor["Location"])
        if not match:
            raise ValueError(f"{self.database}.{table} is not in S3: {descriptor['Location']}")
        bucket, prefix = match.groups()
        return bucket, prefix.rstrip("/") + "/"

    def files(self, table: str) -> tuple[str, list[str]]:
        bucket, prefix = self.location(table)
        keys = [
            obj["Key"]
            for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
            for obj in page.get("Contents", [])
            if obj["Key"].endswith(".parquet") and not obj["Key"].rsplit("/", 1)[-1].startswith(("_", "."))
        ]
        if not keys:
            raise ValueError(f"no Parquet files under s3://{bucket}/{prefix}")
        return bucket, sorted(keys)

    def batches(self, spec: TableSpec, batch_rows: int, columns: list[str] | None = None) -> Iterator[list[dict]]:
        import pyarrow.parquet as pq

        columns = columns or spec.columns
        bucket, keys = self.files(spec.name)
        for n, key in enumerate(keys):
            body = io.BytesIO(self.s3.get_object(Bucket=bucket, Key=key)["Body"].read())
            parquet = pq.ParquetFile(body, coerce_int96_timestamp_unit="us")
            names = parquet.schema_arrow.names
            missing = [c for c in columns if c not in names]
            if missing:
                raise ValueError(f"{spec.name}: s3://{bucket}/{key} has no column {missing}")
            if n == 0:
                unclassified = sorted(set(names) - set(spec.columns) - set(spec.excluded))
                if unclassified:
                    print(f"[{spec.name}] not copied, unclassified columns: {unclassified}", file=sys.stderr)
            for batch in parquet.iter_batches(batch_size=batch_rows, columns=columns):
                yield batch.to_pylist()

    def first_customers(self, n: int, batch_rows: int) -> list[str]:
        """The n smallest customer_ids in customer_360, the same sample for every table."""
        spec = TABLES["customer_360"]
        ids = {row["customer_id"] for batch in self.batches(spec, batch_rows, ["customer_id"]) for row in batch}
        return sorted(i for i in ids if i)[:n]


# ---- Publishing ----


@dataclass
class Stats:
    table: str
    rows_read: int = 0
    rows_served: int = 0
    items_written: int = 0
    sample: dict[str, Any] | None = None

    def line(self, dry_run: bool) -> str:
        written = "dry run, nothing written" if dry_run else f"{self.items_written} items written"
        return f"[{self.table}] {self.rows_read} rows read, {self.rows_served} items built, {written}"


def publish(
    spec: TableSpec,
    batches: Iterable[list[Mapping[str, Any]]],
    table: Any = None,
    customers: frozenset[str] | None = None,
    shard: tuple[int, int] | None = None,
) -> Stats:
    """Builds every item and, given a DynamoDB Table, writes them in batches of 25 (boto3's
    batch_writer, which resends unprocessed items). Without a table it only counts."""
    stats = Stats(spec.name)
    writer = table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) if table is not None else nullcontext()
    queued = 0
    with writer as batch:
        for rows in batches:
            for row in rows:
                stats.rows_read += 1
                if not is_served(spec, row, customers, shard):
                    continue
                try:
                    item = build_item(spec, row)
                except MalformedRowError as error:
                    raise MalformedRowError(f"{spec.name}, source row {stats.rows_read}: {error}") from None
                stats.rows_served += 1
                if stats.sample is None:
                    stats.sample = item
                if batch is not None:
                    batch.put_item(Item=item)
                    queued += 1
    stats.items_written = queued  # the writer has flushed every queued item by now
    return stats


def make_session(profile: str | None, region: str) -> boto3.Session:
    """The named profile when given; otherwise the standard credential chain (env, task role)."""
    if profile:
        return boto3.Session(profile_name=profile, region_name=region)
    return boto3.Session(region_name=region)


def check_account(session: boto3.Session, expected: str) -> None:
    account = session.client("sts").get_caller_identity()["Account"]
    if account != expected:
        raise SystemExit(
            f"These credentials are for AWS account {account}, not the team account {expected}; "
            "the lake and the serving table are only there (the Bedrock profile is another account)."
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Publish the agent zone to the DynamoDB customer-serving table.")
    p.add_argument("--table", action="append", choices=sorted(TABLES), default=[], help="Repeatable.")
    p.add_argument("--all", action="store_true", help="All seven tables.")
    p.add_argument("--dry-run", action="store_true", help="Read and build items; write nothing.")
    p.add_argument("--profile", help="AWS profile, e.g. cuy-loyalty. Default: the standard credential chain.")
    p.add_argument("--region", default=REGION)
    p.add_argument("--database", default=DATABASE)
    p.add_argument("--dynamodb-table", default=SERVING_TABLE)
    p.add_argument("--expected-account", default=TEAM_ACCOUNT)
    p.add_argument("--customer-id", action="append", default=[], help="Load only this customer; repeatable.")
    p.add_argument("--limit-customers", type=int, help="Load only the N smallest customer_ids.")
    p.add_argument("--shard", type=parse_shard, help="K/N: load only customers in shard K of N, so N processes can "
                   "load a customer-scoped table in parallel. Reference tables load in full.")
    p.add_argument("--batch-rows", type=int, default=5000, help="Rows per Parquet record batch.")
    args = p.parse_args(argv)
    if not args.table and not args.all:
        p.error("name a --table or pass --all")
    if args.limit_customers is not None and args.limit_customers < 1:
        p.error("--limit-customers must be at least 1")
    return args


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    specs = list(TABLES.values()) if args.all else [TABLES[t] for t in dict.fromkeys(args.table)]
    session = make_session(args.profile, args.region)
    check_account(session, args.expected_account)
    zone = AgentZone(session, args.database)

    customers = None
    if args.customer_id or args.limit_customers:
        sample = zone.first_customers(args.limit_customers, args.batch_rows) if args.limit_customers else []
        customers = frozenset([*args.customer_id, *sample])
        print(f"Customer-scoped tables limited to {len(customers)} customers; reference tables load in full.")

    table = None
    if not args.dry_run:
        table = session.resource("dynamodb").Table(args.dynamodb_table)
        table.load()  # fails early if the table is missing
    for spec in specs:
        stats = publish(spec, zone.batches(spec, args.batch_rows), table, customers, args.shard)
        shard_note = f" (shard {args.shard[0]}/{args.shard[1]})" if args.shard and spec.scope == "customer" else ""
        print(stats.line(args.dry_run) + shard_note)
        if args.dry_run and stats.sample:
            print(f"    e.g. {stats.sample['PK']} | {stats.sample['SK']} | {sorted(set(stats.sample) - {'PK', 'SK'})}")


if __name__ == "__main__":
    main(sys.argv[1:])
