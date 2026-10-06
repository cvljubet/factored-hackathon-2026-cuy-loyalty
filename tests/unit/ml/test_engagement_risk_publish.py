"""Engagement-risk publisher: item construction, NULL scores, validation, write path and read-back. No AWS."""

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from ml.engagement_risk import config, publish

AS_OF = date(2026, 6, 17)


def gold(**overrides):
    row = {"customer_id": "CLI-A", "model_version": config.MODEL_VERSION, "model_eligible": True, "scoring_source": "model",
           "risk_score": 0.709873, "risk_tier": "high", "reason_code": None, "as_of_date": AS_OF}
    return {**row, **overrides}


FALLBACK = dict(model_eligible=False, scoring_source="fallback", risk_score=np.nan, risk_tier="high",
                reason_code="no_eligible_txn_365d")
NEW = dict(model_eligible=False, scoring_source="fallback", risk_score=None, risk_tier="new_customer",
           reason_code="insufficient_history_new_customer")


def test_model_item():
    item = publish.build_item(gold())
    assert item == {"PK": "CUST#CLI-A", "SK": "ENGAGEMENT_RISK", "model_version": "engagement-risk-v1",
                    "as_of_date": "2026-06-17", "model_eligible": True, "scoring_source": "model",
                    "risk_score": Decimal("0.709873"), "risk_tier": "high"}
    assert "reason_code" not in item and "customer_id" not in item


@pytest.mark.parametrize("fallback", [FALLBACK, NEW], ids=["no_txn_nan", "new_customer_none"])
def test_fallback_items_omit_the_score(fallback):
    item = publish.build_item(gold(customer_id="CLI-B", **fallback))
    assert "risk_score" not in item
    assert (item["model_eligible"], item["scoring_source"], item["risk_tier"], item["reason_code"]) == (
        False, "fallback", fallback["risk_tier"], fallback["reason_code"])


def test_score_is_an_exact_decimal_for_dynamodb():
    item = publish.build_item(gold(risk_score=np.float64(0.1)))
    assert isinstance(item["risk_score"], Decimal) and item["risk_score"] == Decimal("0.1")


def test_items_carry_no_customer_id_or_model_features():
    item = publish.build_item(gold())
    assert set(item) <= {"PK", "SK", *publish.ATTRIBUTES}
    assert not set(item) & set(config.FEATURES)


@pytest.mark.parametrize("bad", [
    {"risk_score": np.nan},                                          # model row without a score
    {**FALLBACK, "risk_score": 0.4},                                 # fallback with a score
    {"customer_id": None}, {"customer_id": "  "}, {"customer_id": "CLI#X"},
])
def test_unusable_rows_are_refused(bad):
    with pytest.raises(ValueError):
        publish.build_item(gold(**bad))


def gold_frame():
    rows = [gold(customer_id=f"CLI-{i}", risk_score=s, risk_tier=t) for i, (s, t) in
            enumerate([(0.8, "high"), (0.5, "medium"), (0.1, "low")])]
    rows += [gold(customer_id="CLI-F1", **FALLBACK), gold(customer_id="CLI-F2", **NEW)]
    return pd.DataFrame(rows)


def test_valid_gold_passes():
    publish.validate_gold(gold_frame(), AS_OF)


@pytest.mark.parametrize("break_it", ["duplicate", "fallback_score", "version"])
def test_invalid_gold_is_refused(break_it):
    df = gold_frame()
    if break_it == "duplicate":
        df = pd.concat([df, df.iloc[[0]]])
    elif break_it == "fallback_score":
        df.loc[df.customer_id == "CLI-F1", "risk_score"] = 0.3
    else:
        df.loc[0, "model_version"] = "engagement-risk-v0"
    with pytest.raises(ValueError):
        publish.validate_gold(df, AS_OF)


def test_counts():
    c = publish.counts(publish.build_item(r) for r in gold_frame().to_dict("records"))
    assert (c["items"], c["model"], c["fallback"]) == (5, 3, 2)
    assert c["model_tiers"] == {"high": 1, "medium": 1, "low": 1}
    assert c["fallback_reasons"] == {"no_eligible_txn_365d": 1, "insufficient_history_new_customer": 1}


class FakeTable:
    def __init__(self, store):
        self.store = store

    def batch_writer(self, overwrite_by_pkeys):
        assert overwrite_by_pkeys == ["PK", "SK"]
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def put_item(self, Item):
        self.store[(Item["PK"], Item["SK"])] = Item


def test_writes_are_idempotent_puts_of_engagement_risk_only():
    store = {("CUST#CLI-0", "PROFILE"): {"first_name": "Ana"}}
    items = [publish.build_item(r) for r in gold_frame().to_dict("records")]
    for _ in range(2):  # a rerun replaces the same keys
        assert publish.write_items(lambda: FakeTable(store), items, workers=3) == 5
    assert len(store) == 6 and store[("CUST#CLI-0", "PROFILE")] == {"first_name": "Ana"}
    with pytest.raises(ValueError, match="only writes"):
        publish.write_items(lambda: FakeTable(store), [{**items[0], "SK": "PROFILE"}], workers=1)


class FakeClient:
    """batch_get_item over a store of plain items, returning half the keys as unprocessed on the first call."""

    def __init__(self, store):
        self.store, self.calls = store, 0

    def batch_get_item(self, RequestItems):
        from boto3.dynamodb.types import TypeSerializer

        ser = TypeSerializer()
        (table, req), = RequestItems.items()
        keys = req["Keys"]
        self.calls += 1
        served, rest = (keys[: len(keys) // 2], keys[len(keys) // 2:]) if self.calls == 1 else (keys, [])
        found = [self.store[(k["PK"]["S"], k["SK"]["S"])] for k in served if (k["PK"]["S"], k["SK"]["S"]) in self.store]
        resp = {"Responses": {table: [{a: ser.serialize(v) for a, v in i.items()} for i in found]}}
        if rest:
            resp["UnprocessedKeys"] = {table: {"Keys": rest, "ConsistentRead": True}}
        return resp


def test_read_back_retries_unprocessed_keys_and_compare_spots_differences():
    items = [publish.build_item(r) for r in gold_frame().to_dict("records")]
    store = {(i["PK"], i["SK"]): i for i in items}
    found = publish.read_back(lambda: FakeClient(store), "t", [{"PK": i["PK"], "SK": "ENGAGEMENT_RISK"} for i in items], workers=1)
    assert len(found) == 5 and publish.compare(items, found) == []
    del store[(items[0]["PK"], "ENGAGEMENT_RISK")]
    store[(items[1]["PK"], "ENGAGEMENT_RISK")] = {**items[1], "risk_tier": "low"}
    found = publish.read_back(lambda: FakeClient(store), "t", [{"PK": i["PK"], "SK": "ENGAGEMENT_RISK"} for i in items], workers=1)
    problems = publish.compare(items, found)
    assert len(problems) == 2 and "missing" in problems[0]
