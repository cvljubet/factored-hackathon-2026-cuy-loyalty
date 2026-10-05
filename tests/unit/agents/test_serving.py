"""The tools over the DynamoDB serving table, against an in-process fake of the table (no AWS)."""

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolReturnPart

from agents import tools
from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import NotReadyRecommendationProvider
from agents.serving import DynamoServingRepository, InMemoryServingRepository, customer_pk
from agents.text import city_key
from agent_testkit import CUSTOMER_ID, OTHER_CUSTOMER_ID, ScriptedModel, answer, call_tool, make_context, make_deps

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "data" / "pipelines" / "serving"))
import load_customer_serving as loader  # noqa: E402


class FakeTable:
    """Just enough of a boto3 DynamoDB Table: GetItem (with projection) and Query on PK with an
    optional begins_with on SK, in SK order, with Limit and pagination. Records every request."""

    def __init__(self, items, page_size=100):
        self.items = sorted(items, key=lambda i: (i["PK"], i["SK"]))
        self.page_size = page_size
        self.requests = []

    def get_item(self, Key, ProjectionExpression=None, ExpressionAttributeNames=None):
        self.requests.append({"op": "get_item", "PK": Key["PK"], "SK": Key["SK"],
                              "fields": sorted((ExpressionAttributeNames or {}).values()) or None})
        for item in self.items:
            if (item["PK"], item["SK"]) == (Key["PK"], Key["SK"]):
                if ExpressionAttributeNames:
                    return {"Item": {k: v for k, v in item.items() if k in ExpressionAttributeNames.values()}}
                return {"Item": dict(item)}
        return {}

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None, ExclusiveStartKey=None):
        built = ConditionExpressionBuilder().build_expression(KeyConditionExpression, is_key_condition=True)
        pk, *prefix = built.attribute_value_placeholders.values()
        prefix = prefix[0] if prefix else ""
        self.requests.append({"op": "query", "PK": pk, "prefix": prefix, "newest_first": not ScanIndexForward,
                              "limit": Limit})
        matching = [i for i in self.items if i["PK"] == pk and i["SK"].startswith(prefix)]
        if not ScanIndexForward:
            matching.reverse()
        if ExclusiveStartKey:
            keys = [(i["PK"], i["SK"]) for i in matching]
            matching = matching[keys.index((ExclusiveStartKey["PK"], ExclusiveStartKey["SK"])) + 1:]
        size = min(Limit or self.page_size, self.page_size)
        page = matching[:size]
        response = {"Items": [dict(i) for i in page]}
        if len(matching) > size:
            response["LastEvaluatedKey"] = {"PK": page[-1]["PK"], "SK": page[-1]["SK"]}
        return response


def txn(customer, ts, n, status="Approved", code="00"):
    return {"PK": f"CUST#{customer}", "SK": f"TXN#{ts}#TRX-{n}", "transaction_date": ts, "amount": Decimal("12.50"),
            "currency": "MXN", "transaction_status": status, "bk_response_code": code, "customer_id": customer}


def lake_items():
    """Items shaped as the loader writes them, for two customers and the reference partitions."""
    own, other = f"CUST#{CUSTOMER_ID}", f"CUST#{OTHER_CUSTOMER_ID}"
    profile = {
        "PK": own, "SK": "PROFILE", "first_name": "Ana", "last_name": "Ruiz", "city": "Ciudad de México",
        "state": "CDMX", "country": "México", "as_of_date": "2026-06-18", "txn_count_90d": Decimal("12"),
        "txn_amount_usd_90d": Decimal("310.5"), "foreign_txn_count_90d": Decimal("1"),
        "spend_usd_food": Decimal("100.25"), "spend_usd_transport": Decimal("0"), "agent_name": "Luis G.",
        "agent_specialty": "Tarjetas", "agent_languages": "español",
        "products": [{"product_type": "Tarjeta Crédito", "product_number_masked": "****4444",
                      "current_balance": Decimal("5731.15"), "bk_is_overdue": True}],
    }
    return [
        profile,
        {"PK": other, "SK": "PROFILE", "first_name": "Bruno", "city": "Bogotá"},
        *[txn(CUSTOMER_ID, f"2026-06-{d:02d}T10:00:00Z", d) for d in range(1, 26)],
        txn(CUSTOMER_ID, "2026-06-30T09:00:00Z", 99, status="Declined", code="51"),
        txn(OTHER_CUSTOMER_ID, "2026-07-01T00:00:00Z", 500),
        {"PK": own, "SK": "CONTACT#2026-05-01T08:00:00Z#INT-1", "interaction_date": "2026-05-01T08:00:00Z",
         "contact_reason": "Producto"},
        {"PK": own, "SK": "COMPLAINT#2025-10-04T03:32:00Z#CMP-1", "complaint_id": "CMP-1", "status": "Open"},
        {"PK": other, "SK": "CAMPAIGN#2026-06-08T07:25:13Z#SND-1", "campaign_name": "CMP_RET_SAV", "promoted_product": "Cuenta Ahorro"},
        {"PK": "REF#BRANCH", "SK": "ciudad_de_mexico#SUC-1", "branch_name": "Centro", "city": "Ciudad de México"},
        {"PK": "REF#BRANCH", "SK": "ciudad_de_mexico#SUC-2", "branch_name": "Sur", "city": "Ciudad de México"},
        {"PK": "REF#BRANCH", "SK": "bogota#SUC-3", "branch_name": "Norte", "city": "Bogotá"},
        {"PK": "REF#FX", "SK": "USD#MXN", "date": "2026-06-17", "source_currency": "USD", "target_currency": "MXN",
         "buy_rate": Decimal("16.812743"), "sell_rate": Decimal("17.229695")},
        {"PK": "REF#FX", "SK": "USD#COP", "date": "2026-06-17", "source_currency": "USD", "target_currency": "COP",
         "buy_rate": Decimal("4025.84"), "sell_rate": Decimal("4083.55")},
    ]


@pytest.fixture
def table():
    return FakeTable(lake_items())


def deps_for(table, customer_id=CUSTOMER_ID):
    return make_deps(DynamoServingRepository(table), customer_id=customer_id)


def run_turn(table, steps, customer_id=CUSTOMER_ID):
    script = ScriptedModel(steps)
    engine = InquiryEngine(script.model, DynamoServingRepository(table), NotReadyRecommendationProvider())
    result = engine.handle(make_context(customer_id), "pregunta")
    returned = [p for m in script.requests[1][0] if isinstance(m, ModelRequest) for p in m.parts
                if isinstance(p, ToolReturnPart | RetryPromptPart)] if len(script.requests) > 1 else []
    return result, returned


# ---- Identity: the PK comes from the verified context only ----


@pytest.mark.parametrize("customer", [CUSTOMER_ID, OTHER_CUSTOMER_ID])
def test_the_authenticated_customer_builds_the_pk(table, customer):
    run_turn(table, [call_tool("get_my_transactions"), answer("ok")], customer_id=customer)

    assert [r["PK"] for r in table.requests] == [f"CUST#{customer}"]


@pytest.mark.parametrize("key", ["customer_id", "customerId", "pk", "PK"])
def test_a_model_supplied_customer_or_key_is_rejected_and_nothing_is_read(table, key):
    result, [retry] = run_turn(table, [call_tool("get_my_transactions", {key: f"CUST#{OTHER_CUSTOMER_ID}"}),
                                       answer("No pude.")])

    assert isinstance(retry, RetryPromptPart) and "extra_forbidden" in str(retry.content)
    assert table.requests == []


def test_tool_arguments_never_reach_the_partition_key(table):
    run_turn(table, [call_tool("get_branch_info", {"city": f"x#CUST#{OTHER_CUSTOMER_ID}"}), answer("ok")])

    assert {r["PK"] for r in table.requests} == {"REF#BRANCH"}


def test_a_key_separator_in_the_identity_is_refused():
    with pytest.raises(ValueError):
        customer_pk(f"{CUSTOMER_ID}#SESSION#x")
    with pytest.raises(ValueError):
        customer_pk("")


# ---- Access patterns ----


@pytest.mark.parametrize(
    ("tool", "prefix", "limit"),
    [
        ("get_my_transactions", "TXN#", tools.DEFAULT_TRANSACTIONS),
        ("get_my_contacts", "CONTACT#", tools.MAX_CONTACTS),
        ("get_my_complaints", "COMPLAINT#", tools.MAX_COMPLAINTS),
        ("get_my_campaigns", "CAMPAIGN#", tools.MAX_CAMPAIGNS),
    ],
)
def test_event_tools_query_their_sk_prefix_newest_first(table, tool, prefix, limit):
    getattr(tools, tool)(deps_for(table))

    assert table.requests == [
        {"op": "query", "PK": f"CUST#{CUSTOMER_ID}", "prefix": prefix, "newest_first": True, "limit": limit}
    ]


@pytest.mark.parametrize(
    ("tool", "fields"),
    [
        ("get_my_profile", tools.PROFILE_FIELDS),
        ("get_my_products", ("products",)),
        ("get_my_agent", tools.AGENT_FIELDS),
    ],
)
def test_profile_tools_read_only_their_fields_of_the_profile_item(table, tool, fields):
    getattr(tools, tool)(deps_for(table))

    assert table.requests == [{"op": "get_item", "PK": f"CUST#{CUSTOMER_ID}", "SK": "PROFILE", "fields": sorted(fields)}]


def test_newest_transactions_come_first_and_are_capped(table):
    default = tools.get_my_transactions(deps_for(table)).data
    capped = tools.get_my_transactions(deps_for(table), limit=50).data

    dates = [t["transaction_date"] for t in default["transactions"]]
    assert dates == sorted(dates, reverse=True) and dates[0] == "2026-06-30T09:00:00Z"
    assert default["count"] == 10 and default["order"] == "newest_first"
    assert capped["count"] == tools.MAX_TRANSACTIONS == 20
    assert "20 most recent" in capped["note"]


def test_queries_follow_pagination():
    table = FakeTable(lake_items(), page_size=7)

    transactions = tools.get_my_transactions(deps_for(table), limit=20).data["transactions"]

    assert len(transactions) == 20 and len({t["transaction_date"] for t in transactions}) == 20
    assert [r["limit"] for r in table.requests] == [20, 13, 6]


def test_declined_transactions_get_a_reason_and_lose_the_raw_code(table):
    [declined] = [t for t in tools.get_my_transactions(deps_for(table)).data["transactions"]
                  if t["transaction_status"] == "Declined"]

    assert declined["decline_reason"] == "insufficient_funds"
    assert "bk_response_code" not in declined


def test_no_current_campaign_is_an_empty_answer_not_an_error(table):
    result = tools.get_my_campaigns(deps_for(table))

    assert (result.status, result.data) == ("ok", {"current_campaigns": [], "count": 0})
    other = tools.get_my_campaigns(deps_for(table, OTHER_CUSTOMER_ID))
    assert other.data["count"] == 1 and other.data["current_campaigns"][0]["promoted_product"] == "Cuenta Ahorro"


def test_spending_states_its_90_day_window(table):
    data = tools.get_my_spending(deps_for(table), months=12).data

    assert (data["window_days"], data["period_start"], data["period_end"]) == (90, "2026-03-20", "2026-06-18")
    assert data["by_category"] == {"food": 100.25, "transport": 0}
    assert (data["approved_transactions"], data["total_spent"]) == (12, 310.5)
    assert data["requested_months"] == 12 and "90 days" in data["note"]
    assert "note" not in tools.get_my_spending(deps_for(table), months=3).data


def test_missing_profile_is_unavailable_not_invented(table):
    for tool in ("get_my_profile", "get_my_products", "get_my_agent", "get_my_spending"):
        result = getattr(tools, tool)(deps_for(table, "CUST-NOBODY"))
        assert (result.status, result.reason, result.data) == ("unavailable", "profile_not_found", None)


def test_a_failing_table_becomes_an_error_result():
    class Down:
        def query(self, **kwargs):
            raise RuntimeError("throttled")

    assert tools.get_my_contacts(deps_for(Down())).reason == "tool_failed"


# ---- Reference data ----


@pytest.mark.parametrize(
    "city", ["Ciudad de México", "Bogotá", "Medellín", "MEDELLIN", "  São   Paulo ", "Querétaro", "La Plata", "x#y", "?"]
)
def test_branch_city_keys_match_the_loader(city):
    try:
        expected = loader.city_key(city)
    except loader.MalformedRowError:
        expected = ""
    assert city_key(city) == expected


def test_branches_are_queried_by_normalized_city(table):
    data = tools.get_branch_info(deps_for(table), city="ciudad de mexico").data

    assert table.requests == [{"op": "query", "PK": "REF#BRANCH", "prefix": "ciudad_de_mexico#",
                               "newest_first": False, "limit": None}]
    assert [b["branch_name"] for b in data["branches"]] == ["Centro", "Sur"] and data["total_in_city"] == 2


def test_branches_default_to_the_customers_city_and_list_cities_when_none_match(table):
    assert tools.get_branch_info(deps_for(table)).data["total_in_city"] == 2  # Ana lives in Ciudad de México
    none = tools.get_branch_info(deps_for(table), city="Lima").data
    assert none["branches"] == [] and none["cities_with_branches"] == ["Bogotá", "Ciudad de México"]


def test_fx_pair_lookup(table):
    result = tools.get_exchange_rate(deps_for(table), "USD", "MXN")

    assert table.requests == [{"op": "get_item", "PK": "REF#FX", "SK": "USD#MXN", "fields": None}]
    [rate] = result.data["rates"]
    assert (rate["buy_rate"], rate["sell_rate"], rate["date"]) == (16.812743, 17.229695, "2026-06-17")


def test_fx_partial_and_missing_pairs(table):
    assert len(tools.get_exchange_rate(deps_for(table), base_currency="USD").data["rates"]) == 2
    assert [r["target_currency"] for r in tools.get_exchange_rate(deps_for(table), quote_currency="COP").data["rates"]] == ["COP"]
    missing = tools.get_exchange_rate(deps_for(table), "USD", "PEN")
    assert (missing.status, missing.reason) == ("unavailable", "currency_pair_not_available")


# ---- What reaches the model ----


def test_no_key_customer_id_or_backend_field_reaches_the_model(table):
    """Every tool, run by the agent, as the model receives the results."""
    calls = [call_tool(name, call_id=f"c{i}") for i, name in enumerate(tools.TOOL_NAMES) if name != "recommend_products"]
    script = ScriptedModel([ModelResponse(parts=[p for c in calls for p in c.parts]), answer("ok")])
    engine = InquiryEngine(script.model, DynamoServingRepository(table), NotReadyRecommendationProvider())

    engine.handle(make_context(), "todo")

    returns = [p for p in script.requests[1][0][-1].parts if isinstance(p, ToolReturnPart)]
    assert len(returns) == len(calls) and all(r.content.status == "ok" for r in returns)
    seen = json.dumps([r.content.model_dump(mode="json") for r in returns])
    for leaked in ('"PK"', '"SK"', "bk_", "customer_id", CUSTOMER_ID, "CUST#", "REF#"):
        assert leaked not in seen
    assert "****4444" in seen and "insufficient_funds" in seen


def test_public_strips_nested_backend_fields_and_converts_numbers():
    value = {"PK": "x", "SK": "y", "customer_id": "c", "bk_a": 1,
             "products": [{"bk_is_overdue": True, "n": Decimal("2"), "f": Decimal("2.5")}]}

    assert tools.public(value) == {"products": [{"n": 2, "f": 2.5}]}


def test_in_memory_repository_orders_events_and_strips_keys():
    repo = InMemoryServingRepository(events={"transactions": {"C": [
        {"PK": "CUST#C", "transaction_date": "2026-01-01T00:00:00Z"},
        {"PK": "CUST#C", "transaction_date": "2026-03-01T00:00:00Z"},
    ]}})

    assert repo.get_transactions("C", 5) == [{"transaction_date": "2026-03-01T00:00:00Z"},
                                             {"transaction_date": "2026-01-01T00:00:00Z"}]
    assert repo.get_transactions("OTHER", 5) == []
