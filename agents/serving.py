"""Where the tools read customer and reference data: the DynamoDB customer-serving table, or an
in-memory stand-in for tests and local runs without AWS.

The table (loaded by data/pipelines/serving/load_customer_serving.py from the agent zone):

    PK = CUST#<customer_id>  SK = PROFILE | TXN#<ts>#<id> | CONTACT#<ts>#<id> | COMPLAINT#<ts>#<id> | CAMPAIGN#<ts>#<id>
    PK = REF#BRANCH          SK = <city_key>#<branch_id>
    PK = REF#FX              SK = <source_currency>#<target_currency>

Every customer method takes the customer_id the tools read from AgentDeps (the verified token);
a repository never decides who may read what. Results come without PK and SK. Backend-only
fields (bk_*) are kept for deterministic logic; the tools strip them before the model sees a result.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from agents.text import city_key as key_of

Item = dict[str, Any]
STORAGE_KEYS = frozenset({"PK", "SK", "customer_id"})


class ServingRepository(Protocol):
    def get_profile(self, customer_id: str, fields: Sequence[str] | None = None) -> Mapping[str, Any] | None:
        """The customer's PROFILE item (only `fields` when given), or None if there is none."""
        ...

    def get_transactions(self, customer_id: str, limit: int) -> list[Item]:
        """The customer's most recent transactions, newest first."""
        ...

    def get_contacts(self, customer_id: str, limit: int) -> list[Item]: ...

    def get_complaints(self, customer_id: str, limit: int) -> list[Item]: ...

    def get_campaigns(self, customer_id: str, limit: int) -> list[Item]:
        """The campaigns running on the as-of date (the loader serves only those), newest send first."""
        ...

    def get_branches(self, city_key: str | None = None) -> list[Item]:
        """The branches in one city (by its city_key), or every branch."""
        ...

    def get_fx_rate(self, source_currency: str, target_currency: str) -> Item | None: ...

    def get_fx_rates(self) -> list[Item]: ...


def customer_pk(customer_id: str) -> str:
    """CUST#<customer_id>. A separator in the id is refused, so no id can name another key."""
    if not customer_id or "#" in customer_id:
        raise ValueError("customer_id must be non-empty and must not contain '#'")
    return f"CUST#{customer_id}"


def _without_keys(item: Mapping[str, Any]) -> Item:
    return {key: value for key, value in item.items() if key not in STORAGE_KEYS}


class DynamoServingRepository:
    """Reads the customer-serving table: GetItem for PROFILE and FX pairs, Query on PK with a
    begins_with sort-key prefix for everything else."""

    def __init__(self, table: Any):
        self.table = table

    def get_profile(self, customer_id: str, fields: Sequence[str] | None = None) -> Mapping[str, Any] | None:
        request: dict[str, Any] = {"Key": {"PK": customer_pk(customer_id), "SK": "PROFILE"}}
        if fields:
            # Placeholders, since names such as "state" are DynamoDB reserved words.
            names = {f"#f{i}": name for i, name in enumerate(fields)}
            request |= {"ProjectionExpression": ", ".join(names), "ExpressionAttributeNames": names}
        item = self.table.get_item(**request).get("Item")
        return None if item is None else _without_keys(item)

    def get_transactions(self, customer_id: str, limit: int) -> list[Item]:
        return self._query(customer_pk(customer_id), "TXN#", limit)

    def get_contacts(self, customer_id: str, limit: int) -> list[Item]:
        return self._query(customer_pk(customer_id), "CONTACT#", limit)

    def get_complaints(self, customer_id: str, limit: int) -> list[Item]:
        return self._query(customer_pk(customer_id), "COMPLAINT#", limit)

    def get_campaigns(self, customer_id: str, limit: int) -> list[Item]:
        return self._query(customer_pk(customer_id), "CAMPAIGN#", limit)

    def get_branches(self, city_key: str | None = None) -> list[Item]:
        return self._query("REF#BRANCH", f"{city_key}#" if city_key else None, None, newest_first=False)

    def get_fx_rate(self, source_currency: str, target_currency: str) -> Item | None:
        item = self.table.get_item(Key={"PK": "REF#FX", "SK": f"{source_currency}#{target_currency}"}).get("Item")
        return None if item is None else _without_keys(item)

    def get_fx_rates(self) -> list[Item]:
        return self._query("REF#FX", None, None, newest_first=False)

    def _query(self, pk: str, prefix: str | None, limit: int | None, newest_first: bool = True) -> list[Item]:
        """Items under pk (whose SK starts with prefix), newest SK first, at most limit."""
        condition = Key("PK").eq(pk) & Key("SK").begins_with(prefix) if prefix else Key("PK").eq(pk)
        request: dict[str, Any] = {"KeyConditionExpression": condition, "ScanIndexForward": not newest_first}
        items: list[Item] = []
        while limit is None or len(items) < limit:
            if limit is not None:
                request["Limit"] = limit - len(items)
            page = self.table.query(**request)
            items += [_without_keys(item) for item in page.get("Items", [])]
            if "LastEvaluatedKey" not in page:
                break
            request["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        return items


def dynamodb_serving_table(table_name: str, region: str, profile: str | None = None) -> Any:
    """The serving table, through a boto3 session of its own: the named profile when given (local
    runs against the team account), else the standard chain (the ECS task role). It never shares
    the Bedrock session, which may be in another account. Building it makes no request."""
    session = boto3.Session(profile_name=profile, region_name=region)
    # Short timeouts and few retries, so a slow read fails the tool well within the turn.
    config = Config(connect_timeout=3, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 3})
    return session.resource("dynamodb", config=config).Table(table_name)


# The field each kind of event is ordered by, newest first (the time in its sort key).
_EVENT_TIME = {
    "transactions": "transaction_date",
    "contacts": "interaction_date",
    "complaints": "creation_date",
    "campaigns": "send_date",
}


class InMemoryServingRepository:
    """A ServingRepository over fixed data, for tests and local runs without AWS. Missing data
    reads as none (no profile, no transactions, ...)."""

    def __init__(
        self,
        profiles: Mapping[str, Mapping[str, Any]] | None = None,
        events: Mapping[str, Mapping[str, Iterable[Mapping[str, Any]]]] | None = None,
        branches: Iterable[Mapping[str, Any]] = (),
        fx_rates: Iterable[Mapping[str, Any]] = (),
    ):
        """events: kind ("transactions", "contacts", "complaints", "campaigns") -> customer_id -> items."""
        self.profiles = dict(profiles or {})
        self.events = {kind: {cid: list(items) for cid, items in by_customer.items()} for kind, by_customer in (events or {}).items()}
        self.branches = [dict(b) for b in branches]
        self.fx_rates = [dict(r) for r in fx_rates]

    def get_profile(self, customer_id: str, fields: Sequence[str] | None = None) -> Mapping[str, Any] | None:
        profile = self.profiles.get(customer_id)
        if profile is None:
            return None
        return {k: v for k, v in _without_keys(profile).items() if not fields or k in fields}

    def _events(self, kind: str, customer_id: str, limit: int) -> list[Item]:
        items = self.events.get(kind, {}).get(customer_id, [])
        newest = sorted(items, key=lambda item: str(item.get(_EVENT_TIME[kind], "")), reverse=True)
        return [_without_keys(item) for item in newest[:limit]]

    def get_transactions(self, customer_id: str, limit: int) -> list[Item]:
        return self._events("transactions", customer_id, limit)

    def get_contacts(self, customer_id: str, limit: int) -> list[Item]:
        return self._events("contacts", customer_id, limit)

    def get_complaints(self, customer_id: str, limit: int) -> list[Item]:
        return self._events("complaints", customer_id, limit)

    def get_campaigns(self, customer_id: str, limit: int) -> list[Item]:
        return self._events("campaigns", customer_id, limit)

    def get_branches(self, city_key: str | None = None) -> list[Item]:
        return [dict(b) for b in self.branches if city_key is None or key_of(str(b.get("city", ""))) == city_key]

    def get_fx_rate(self, source_currency: str, target_currency: str) -> Item | None:
        for rate in self.fx_rates:
            if (rate.get("source_currency"), rate.get("target_currency")) == (source_currency, target_currency):
                return dict(rate)
        return None

    def get_fx_rates(self) -> list[Item]:
        return [dict(r) for r in self.fx_rates]
