import json
import re
from datetime import UTC, datetime, timedelta, timezone

import boto3
import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder
from botocore.exceptions import ClientError
from botocore.stub import ANY, Stubber

from agents.conversations import DynamoHandoffStore, DynamoSessionStore, session_pk, sort_time, turn_sk
from agents.engines.escalation import Handoff
from agents.factory import build_orchestrator
from agents.safety import REDACTED
from agents.sessions import MAX_TURNS, ConversationTurn, InMemorySessionStore, SessionConflict, SessionState
from agent_testkit import CUSTOMER_ID, OTHER_CUSTOMER_ID, RecordingServing, ScriptedModel, answer, call_tool, model_down

SESSION = "session-1"
START = datetime(2026, 10, 5, 21, 30, tzinfo=UTC)
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


class Clock:
    def __init__(self, now=START):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def _cancelled(reason):
    return ClientError(
        {
            "Error": {"Code": "TransactionCanceledException", "Message": f"Transaction cancelled [{reason}, None]"},
            "CancellationReasons": [{"Code": reason}, {"Code": "None"}],
        },
        "TransactWriteItems",
    )


class FakeConversationsTable:
    """Just enough of a boto3 Table for the conversations stores: GetItem, Query on PK with BETWEEN on
    SK (descending, Limit), PutItem and TransactWriteItems with the conditions the stores use."""

    name = "cuy-loyalty-dev-conversations"

    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.requests: list[dict] = []
        self.meta = type("Meta", (), {"client": self})()
        self.fail_next_transaction_with: str | None = None

    def get_item(self, Key, ConsistentRead=False):
        self.requests.append({"op": "get_item", **Key, "consistent": ConsistentRead})
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def query(self, KeyConditionExpression, ScanIndexForward=True, Limit=None, ConsistentRead=False):
        built = ConditionExpressionBuilder().build_expression(KeyConditionExpression, is_key_condition=True)
        pk, low, high = built.attribute_value_placeholders.values()
        self.requests.append({"op": "query", "PK": pk, "between": (low, high), "newest_first": not ScanIndexForward,
                              "limit": Limit, "consistent": ConsistentRead})
        matching = sorted((i for (p, s), i in self.items.items() if p == pk and low <= s <= high), key=lambda i: i["SK"])
        if not ScanIndexForward:
            matching.reverse()
        return {"Items": [dict(i) for i in matching[:Limit]]}

    def put_item(self, Item, ConditionExpression):
        assert ConditionExpression == "attribute_not_exists(SK)"
        self.requests.append({"op": "put_item", "PK": Item["PK"], "SK": Item["SK"]})
        if (Item["PK"], Item["SK"]) in self.items:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def transact_write_items(self, TransactItems):
        self.requests.append({"op": "transact", "items": TransactItems})
        if self.fail_next_transaction_with:
            reason, self.fail_next_transaction_with = self.fail_next_transaction_with, None
            raise _cancelled(reason)
        for entry in TransactItems:
            if "Put" in entry:
                item = entry["Put"]["Item"]
                assert entry["Put"]["ConditionExpression"] == "attribute_not_exists(SK)"
                if (item["PK"], item["SK"]) in self.items:
                    raise _cancelled("ConditionalCheckFailed")
            else:
                update = entry["Update"]
                current = self.items.get((update["Key"]["PK"], update["Key"]["SK"]))
                assert update["ConditionExpression"] == "attribute_not_exists(PK) OR #version = :previous"
                if current is not None and current["version"] != update["ExpressionAttributeValues"][":previous"]:
                    raise _cancelled("ConditionalCheckFailed")
        for entry in TransactItems:
            if "Put" in entry:
                item = entry["Put"]["Item"]
                self.items[(item["PK"], item["SK"])] = dict(item)
            else:
                self._apply_update(entry["Update"])

    def _apply_update(self, update):
        key = (update["Key"]["PK"], update["Key"]["SK"])
        item = dict(self.items.get(key, update["Key"]))
        names, values = update["ExpressionAttributeNames"], update["ExpressionAttributeValues"]
        sets, _, removes = update["UpdateExpression"].removeprefix("SET ").partition(" REMOVE ")
        for assignment in re.split(r", (?=#)", sets):  # not inside if_not_exists(#a, :b)
            name, value = assignment.split(" = ", 1)
            if fallback := re.fullmatch(r"if_not_exists\(#\w+, (:\w+)\)", value):
                item.setdefault(names[name], values[fallback.group(1)])
            else:
                item[names[name]] = values[value]
        for name in filter(None, removes.split(", ")):
            item.pop(names[name], None)
        self.items[key] = item

    def of_kind(self, prefix, pk=None):
        return [i for (p, s), i in sorted(self.items.items()) if s.startswith(prefix) and (pk is None or p == pk)]


@pytest.fixture
def table():
    return FakeConversationsTable()


@pytest.fixture
def clock():
    return Clock()


class Chat:
    """The real orchestrator over the DynamoDB stores and a fake table."""

    def __init__(self, table, clock, steps=()):
        self.table = table
        self.model = ScriptedModel(steps)
        self.sessions = DynamoSessionStore(table, idle_minutes=10, retention_days=7, clock=clock)
        self.orchestrator = build_orchestrator(
            serving=RecordingServing(),
            model=self.model.model,
            sessions=self.sessions,
            handoffs=DynamoHandoffStore(table, retention_days=30),
        )

    def say(self, text, customer_id=CUSTOMER_ID, session_id=SESSION):
        return self.orchestrator.handle(customer_id=customer_id, session_id=session_id, user_message=text)


# ---- Keys ----


def test_partition_is_the_verified_customer_and_the_session():
    assert session_pk("CUST-A", "s-1") == "CUST#CUST-A#SESSION#s-1"


@pytest.mark.parametrize("customer_id, session_id", [("CUST-A#SESSION#x", "s-1"), ("CUST-A", "s#1"), ("", "s-1"),
                                                     ("CUST-A", "")])
def test_a_separator_or_empty_id_is_refused_and_nothing_is_read(table, customer_id, session_id):
    with pytest.raises(ValueError):
        DynamoSessionStore(table).load(customer_id, session_id)

    assert table.requests == []


def test_sort_time_is_fixed_width_utc():
    lima_evening = datetime(2026, 10, 5, 16, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert sort_time(lima_evening) == "2026-10-05T21:30:00.000000Z"
    assert sort_time(datetime(2026, 10, 5, 18, 30, 0, 5, tzinfo=UTC)) == "2026-10-05T18:30:00.000005Z"


# ---- Persisting turns and session metadata ----


def test_each_turn_writes_session_metadata_and_both_messages(table, clock):
    Chat(table, clock).say("hola")

    pk = f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}"
    [session] = table.of_kind("SESSION", pk)
    assert (session["version"], session["language"], session["consecutive_failures"]) == (1, "es", 0)
    assert session["created_at"] == session["updated_at"] == START.isoformat()
    assert json.loads(session["opening"])["text"] == "hola"
    turns = table.of_kind("TURN#", pk)
    assert [(t["role"], t["text"]) for t in turns][0] == ("customer", "hola")
    assert [t["role"] for t in turns] == ["customer", "assistant"]
    for turn in turns:
        assert re.fullmatch(r"TURN#\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z#\d{10}\.\d{2}#[0-9a-f]{32}", turn["SK"])
        assert turn["SK"].endswith(turn["message_id"])


def test_items_expire_seven_days_after_the_turn(table, clock):
    Chat(table, clock).say("hola")

    expiry = int((START + timedelta(days=7)).timestamp())
    assert {i["expires_at"] for i in table.items.values()} == {expiry}


def test_a_new_session_loads_empty_without_querying_turns(table):
    assert DynamoSessionStore(table).load(CUSTOMER_ID, SESSION) == SessionState()
    assert [r["op"] for r in table.requests] == ["get_item"]


def test_the_conversation_survives_a_new_process(table, clock):
    Chat(table, clock, [call_tool("get_my_profile"), answer("Vives en Cusco, Ana.")]).say("¿En qué ciudad está registrado mi perfil?")

    state = Chat(table, clock).sessions.load(CUSTOMER_ID, SESSION)

    assert state.version == 1
    assert [(t.role, t.text) for t in state.turns] == [("customer", "¿En qué ciudad está registrado mi perfil?"), ("assistant", "Vives en Cusco, Ana.")]
    [fact] = state.facts
    assert (fact.tool, fact.data["city"]) == ("get_my_profile", "Cusco")
    assert state.opening.text == "¿En qué ciudad está registrado mi perfil?"


def test_load_reads_the_latest_turns_newest_first_and_returns_them_in_order(table, clock):
    chat = Chat(table, clock)
    for i in range(MAX_TURNS):  # 2 * MAX_TURNS messages stored
        clock.advance(seconds=1)
        chat.say(f"pregunta fuera de tema {i}")
    table.requests.clear()

    state = chat.sessions.load(CUSTOMER_ID, SESSION)

    [get, query] = table.requests
    assert (get["op"], get["SK"], get["consistent"]) == ("get_item", "SESSION", True)
    assert (query["newest_first"], query["limit"], query["consistent"]) == (True, MAX_TURNS, True)
    opening_sk = table.items[(f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}", "SESSION")]["opening_sk"]
    assert opening_sk.startswith(f"TURN#{sort_time(state.opening.at)}#") and opening_sk.endswith(state.opening.message_id)
    assert query["between"] == (opening_sk, "TURN#~")  # the window starts exactly at the opening turn
    assert len(state.turns) == MAX_TURNS
    assert state.turns[-1].role == "assistant"
    customer_texts = [t.text for t in state.turns if t.role == "customer"]
    assert customer_texts == [f"pregunta fuera de tema {i}" for i in range(MAX_TURNS // 2, MAX_TURNS)]
    assert len(table.of_kind("TURN#")) == 2 * MAX_TURNS  # nothing is deleted, only the window is read


def test_the_opening_message_is_kept_after_it_leaves_the_window(table, clock):
    chat = Chat(table, clock)
    chat.say("primera pregunta")
    for i in range(MAX_TURNS):
        clock.advance(seconds=1)
        chat.say(f"otra {i}")

    reply = chat.say("Quiero hablar con un asesor")

    [item] = table.of_kind("HANDOFF#")
    handoff = json.loads(item["payload"])
    assert handoff["id"] == reply.handoff_id
    assert handoff["opening_message"]["text"] == "primera pregunta"
    assert "primera pregunta" not in [t["text"] for t in handoff["recent_turns"]]


def test_sessions_of_other_customers_are_not_read(table, clock):
    chat = Chat(table, clock)
    chat.say("¿Quién ganó el partido?", customer_id=OTHER_CUSTOMER_ID)
    table.requests.clear()

    state = chat.sessions.load(CUSTOMER_ID, SESSION)  # same session id, other customer

    assert state == SessionState()
    assert {r["PK"] for r in table.requests} == {f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}"}


# ---- Idle timeout ----


def test_an_idle_session_starts_a_new_conversation_and_hides_older_turns(table, clock):
    chat = Chat(table, clock, [])
    chat.say("¿Quién ganó el partido?")
    chat.say("¿Y ayer?")
    clock.advance(minutes=11)

    assert chat.sessions.load(CUSTOMER_ID, SESSION) == SessionState(version=2)
    chat.say("Quiero hablar con un asesor")

    state = chat.sessions.load(CUSTOMER_ID, SESSION)
    assert state.version == 3
    assert [t.text for t in state.turns][0] == "Quiero hablar con un asesor"
    assert state.opening.text == "Quiero hablar con un asesor"
    handoff = json.loads(table.of_kind("HANDOFF#")[0]["payload"])
    assert [t["text"] for t in handoff["recent_turns"]] == ["Quiero hablar con un asesor"]


def test_a_session_within_the_idle_limit_continues(table, clock):
    chat = Chat(table, clock)
    chat.say("hola")
    clock.advance(minutes=9)

    assert len(chat.sessions.load(CUSTOMER_ID, SESSION).turns) == 2


def test_failures_and_language_are_kept_between_turns(table, clock):
    chat = Chat(table, clock, [model_down(), model_down()])
    chat.say("¿Cuál es mi saldo?")

    state = chat.sessions.load(CUSTOMER_ID, SESSION)
    assert (state.consecutive_failures, state.language) == (1, "es")

    reply = chat.say("¿Cuál es mi saldo?")
    assert reply.status == "escalated"


# ---- Concurrent writes ----


def test_a_stale_save_raises_conflict_and_writes_nothing(table, clock):
    chat = Chat(table, clock)
    chat.say("hola")
    stale = chat.sessions.load(CUSTOMER_ID, SESSION)
    chat.say("otra vez")  # another tab saved version 2
    before = dict(table.items)

    turn = ConversationTurn(role="customer", text="tarde")
    with pytest.raises(SessionConflict):
        chat.sessions.save(CUSTOMER_ID, SESSION, stale.model_copy(update={"version": stale.version + 1}), (turn,))

    assert table.items == before


@pytest.mark.parametrize("reason", ["ConditionalCheckFailed", "TransactionConflict"])
def test_a_cancelled_transaction_is_a_conflict(table, clock, reason):
    table.fail_next_transaction_with = reason

    with pytest.raises(SessionConflict):
        Chat(table, clock).say("hola")


def test_other_errors_are_not_hidden_as_conflicts(table, clock):
    table.fail_next_transaction_with = "ValidationError"

    with pytest.raises(ClientError):
        Chat(table, clock).say("hola")


def test_in_memory_store_applies_the_same_version_rule():
    store = InMemorySessionStore()
    store.save(CUSTOMER_ID, SESSION, SessionState(version=1), ())

    with pytest.raises(SessionConflict):
        store.save(CUSTOMER_ID, SESSION, SessionState(version=1), ())
    store.save(CUSTOMER_ID, SESSION, SessionState(version=2), ())
    assert store.load(CUSTOMER_ID, SESSION).version == 2


# ---- Handoffs ----


def test_a_handoff_is_its_own_item_and_the_session_records_it(table, clock):
    chat = Chat(table, clock)
    reply = chat.say("Quiero hablar con un asesor")

    [item] = table.of_kind("HANDOFF#", f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}")
    assert re.fullmatch(rf"HANDOFF#\d{{4}}-\d\d-\d\dT[\d:.]{{15}}Z#{reply.handoff_id}", item["SK"])
    assert (item["handoff_id"], item["reason"], item["status"], item["language"]) == (
        reply.handoff_id, "human_requested", "pending", "es")
    payload = json.loads(item["payload"])
    assert (payload["customer_id"], payload["session_id"]) == (CUSTOMER_ID, SESSION)
    created = datetime.fromisoformat(item["created_at"])
    assert item["expires_at"] == int((created + timedelta(days=30)).timestamp())

    state = chat.sessions.load(CUSTOMER_ID, SESSION)
    assert (state.handoff_count, state.last_handoff_id, state.last_handoff_reason) == (
        1, reply.handoff_id, "human_requested")


def test_the_handoff_stays_when_the_session_save_conflicts(table, clock):
    table.fail_next_transaction_with = "ConditionalCheckFailed"

    with pytest.raises(SessionConflict):
        Chat(table, clock).say("Quiero hablar con un asesor")

    assert len(table.of_kind("HANDOFF#")) == 1
    assert table.of_kind("SESSION") == table.of_kind("TURN#") == []


# ---- What is never stored ----


def test_no_secret_token_or_trace_reaches_the_table(table, clock):
    chat = Chat(table, clock, [call_tool("get_my_profile"), answer("Vives en Cusco, Ana.")])
    chat.say(f"mi clave es Hunter2! y mi token Bearer {JWT}")
    chat.say("Quiero hablar con un asesor")

    stored = json.dumps(list(table.items.values()), default=str)
    assert "Hunter2!" not in stored and JWT not in stored
    assert REDACTED in stored
    for forbidden in ("blocked_reason", "route", "tools_called", "model_requests", "trace", "token", "bk_"):
        assert forbidden not in {key for item in table.items.values() for key in item}
    assert "bk_" not in stored


# ---- Request shapes, checked against the real DynamoDB API model ----


@pytest.fixture
def real_table():
    return boto3.Session(region_name="us-east-2").resource("dynamodb").Table("cuy-loyalty-dev-conversations")


def test_save_sends_one_valid_transaction(real_table, clock):
    store = DynamoSessionStore(real_table, clock=clock)
    pk = f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}"
    turns = (ConversationTurn(role="customer", text="hola", at=START), ConversationTurn(role="assistant", text="¡Hola!", at=START))
    state = SessionState(version=1, language="es", turns=turns, opening=turns[0])
    put = {"Put": {"TableName": real_table.name, "Item": ANY, "ConditionExpression": "attribute_not_exists(SK)"}}
    with Stubber(real_table.meta.client) as stub:
        stub.add_response("transact_write_items", {}, {"TransactItems": [
            {"Update": {
                "TableName": real_table.name,
                "Key": {"PK": pk, "SK": "SESSION"},
                "UpdateExpression": ANY,
                "ConditionExpression": "attribute_not_exists(PK) OR #version = :previous",
                "ExpressionAttributeNames": ANY,
                "ExpressionAttributeValues": ANY,
            }},
            put,
            put,
        ]})
        store.save(CUSTOMER_ID, SESSION, state, turns)
        stub.assert_no_pending_responses()


def test_update_expression_names_every_placeholder_it_uses(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    store.save(CUSTOMER_ID, SESSION, SessionState(version=1), ())

    update = table.requests[-1]["items"][0]["Update"]
    used_names = set(re.findall(r"#\w+", update["UpdateExpression"] + " " + update["ConditionExpression"]))
    used_values = set(re.findall(r":\w+", update["UpdateExpression"] + " " + update["ConditionExpression"]))
    assert used_names == set(update["ExpressionAttributeNames"])  # DynamoDB refuses unused or missing ones
    assert used_values == set(update["ExpressionAttributeValues"])
    # Unset optional fields are removed, so a reset session does not keep an old language or handoff.
    removed = update["UpdateExpression"].partition(" REMOVE ")[2].split(", ")
    assert {"#language", "#opening", "#last_handoff_id", "#last_handoff_reason"} <= set(removed)


def test_load_sends_a_consistent_get_and_a_bounded_query(real_table, clock):
    store = DynamoSessionStore(real_table, clock=clock)
    pk = f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}"
    opening = ConversationTurn(role="customer", text="hola", at=START)
    session = {"PK": pk, "SK": "SESSION", "version": 1, "updated_at": START.isoformat(),
               "opening": opening.model_dump_json(), "facts": "[]", "language": "es"}
    with Stubber(real_table.meta.client) as stub:
        stub.add_response("get_item", {"Item": {k: {"N": str(v)} if isinstance(v, int) else {"S": v}
                                                for k, v in session.items()}},
                          {"TableName": real_table.name, "Key": {"PK": pk, "SK": "SESSION"}, "ConsistentRead": True})
        stub.add_response("query", {"Items": []}, {
            "TableName": real_table.name, "KeyConditionExpression": ANY, "ScanIndexForward": False,
            "Limit": MAX_TURNS, "ConsistentRead": True})
        state = store.load(CUSTOMER_ID, SESSION)
        stub.assert_no_pending_responses()

    assert (state.version, state.language, state.opening) == (1, "es", opening)


def test_handoff_put_is_valid_and_conditional(real_table):
    handoff = Handoff(id="HO-1", customer_id=CUSTOMER_ID, session_id=SESSION, reason="credit_decision",
                      language="pt", created_at=START, open_question="crédito?")
    with Stubber(real_table.meta.client) as stub:
        stub.add_response("put_item", {}, {
            "TableName": real_table.name,
            "Item": {
                "PK": f"CUST#{CUSTOMER_ID}#SESSION#{SESSION}",
                "SK": "HANDOFF#2026-10-05T21:30:00.000000Z#HO-1",
                "handoff_id": "HO-1", "reason": "credit_decision", "status": "pending", "language": "pt",
                "created_at": START.isoformat(), "payload": handoff.model_dump_json(),
                "expires_at": int((START + timedelta(days=30)).timestamp()),
            },
            "ConditionExpression": "attribute_not_exists(SK)",
        })
        DynamoHandoffStore(real_table, retention_days=30).create(handoff)
        stub.assert_no_pending_responses()


# ---- Turn order when timestamps tie ----
# The customer message and its reply are often created within one clock tick (about 1-16 ms on Windows).
# Equal times must keep the save order, never the random message_id's order.

TICK = START + timedelta(seconds=5)
HIGH_ID, LOW_ID = "f" * 32, "0" * 32  # if the id decided ties, the second turn would sort first


def turn(role, text, at=TICK, message_id=None):
    return ConversationTurn(role=role, text=text, at=at, **({"message_id": message_id} if message_id else {}))


def save(store, version, opening, *turns):
    store.save(CUSTOMER_ID, SESSION, SessionState(version=version, opening=opening), new_turns=turns)


def roles_and_texts(state):
    return [(t.role, t.text) for t in state.turns]


def test_a_reply_with_the_same_timestamp_stays_after_its_question(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    question = turn("customer", "hola", message_id=HIGH_ID)
    reply = turn("assistant", "¿En qué te ayudo?", message_id=LOW_ID)
    save(store, 1, question, question, reply)

    assert roles_and_texts(store.load(CUSTOMER_ID, SESSION)) == [("customer", "hola"), ("assistant", "¿En qué te ayudo?")]
    customer_sk, assistant_sk = (t["SK"] for t in table.of_kind("TURN#"))
    assert customer_sk.startswith(f"TURN#{sort_time(TICK)}#0000000001.00#") and assistant_sk.endswith(f".01#{LOW_ID}")


def test_many_turns_sharing_one_timestamp_keep_their_save_order(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    opening = turn("customer", "q1", message_id="f" * 32)
    for version in range(1, 4):
        q = opening if version == 1 else turn("customer", f"q{version}", message_id=f"{9 - version:x}" * 32)
        a = turn("assistant", f"a{version}", message_id=f"{5 - version:x}" * 32)
        save(store, version, opening, q, a)

    assert roles_and_texts(store.load(CUSTOMER_ID, SESSION)) == [
        ("customer", "q1"), ("assistant", "a1"), ("customer", "q2"), ("assistant", "a2"), ("customer", "q3"), ("assistant", "a3")]


def test_the_latest_window_is_cut_correctly_when_every_turn_shares_a_timestamp(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    opening = turn("customer", "q1")
    exchanges = MAX_TURNS // 2 + 1  # one exchange more than the window holds
    for version in range(1, exchanges + 1):
        q = opening if version == 1 else turn("customer", f"q{version}")
        save(store, version, opening, q, turn("assistant", f"a{version}"))

    state = store.load(CUSTOMER_ID, SESSION)
    expected = [(role, f"{role[0] if role == 'assistant' else 'q'}{v}".replace("customer", "q"))
                for v in range(2, exchanges + 1) for role in ("customer", "assistant")]
    assert roles_and_texts(state) == [(r, t) for r, t in expected]
    assert len(state.turns) == MAX_TURNS and state.turns[0].text == "q2" and state.turns[-1].text == f"a{exchanges}"


def test_repeated_reads_return_the_same_order(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    opening = turn("customer", "hola", message_id=HIGH_ID)
    save(store, 1, opening, opening, turn("assistant", "hola, ¿qué consultas?", message_id=LOW_ID))
    save(store, 2, opening, turn("customer", "mi perfil", message_id="e" * 32), turn("assistant", "Ana, Cusco", message_id="1" * 32))

    first = store.load(CUSTOMER_ID, SESSION).turns
    assert all(store.load(CUSTOMER_ID, SESSION).turns == first for _ in range(50))
    assert [t.text for t in first] == ["hola", "hola, ¿qué consultas?", "mi perfil", "Ana, Cusco"]


def test_distinct_timestamps_still_order_by_time_whatever_the_ids(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    early, later = START + timedelta(seconds=1), START + timedelta(seconds=2)
    opening = turn("customer", "primero", at=early, message_id="f" * 32)
    # Unique ids, deliberately in the opposite order to time.
    save(store, 1, opening, opening, turn("assistant", "segundo", at=later, message_id="0" * 32))
    save(store, 2, opening, turn("customer", "tercero", at=later + timedelta(milliseconds=1), message_id="1" * 32),
         turn("assistant", "cuarto", at=later + timedelta(seconds=1), message_id="e" * 32))

    assert [t.text for t in store.load(CUSTOMER_ID, SESSION).turns] == ["primero", "segundo", "tercero", "cuarto"]
    assert table.requests[-1]["between"] == (turn_sk(opening, 1, 0), "TURN#~")  # starts at the opening turn


def test_turns_saved_before_the_sequence_existed_still_load_in_time_order(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    pk = session_pk(CUSTOMER_ID, SESSION)
    old_q = turn("customer", "antes", at=START + timedelta(seconds=1), message_id="f" * 32)
    old_a = turn("assistant", "respuesta antigua", at=START + timedelta(seconds=2), message_id="0" * 32)
    for old in (old_q, old_a):  # the previous key format: TURN#<ts>#<message_id>
        table.items[(pk, f"TURN#{sort_time(old.at)}#{old.message_id}")] = {
            "PK": pk, "SK": f"TURN#{sort_time(old.at)}#{old.message_id}", "role": old.role, "text": old.text,
            "at": old.at.isoformat(), "message_id": old.message_id, "expires_at": 1}
    table.items[(pk, "SESSION")] = {"PK": pk, "SK": "SESSION", "version": 1, "updated_at": START.isoformat(),
                                     "opening": old_q.model_dump_json(), "facts": "[]"}
    save(store, 2, old_q, turn("customer", "después"), turn("assistant", "respuesta nueva"))

    assert [t.text for t in store.load(CUSTOMER_ID, SESSION).turns] == ["antes", "respuesta antigua", "después", "respuesta nueva"]


def test_turn_sort_key_format():
    t = turn("customer", "x", message_id="a" * 32)
    assert turn_sk(t, 7, 1) == f"TURN#{sort_time(TICK)}#0000000007.01#{'a' * 32}"
    assert turn_sk(t, 7, 0) < turn_sk(t, 7, 1) < turn_sk(t, 8, 0) < "TURN#~"


def test_the_opening_sort_key_is_recorded_once_and_kept(table, clock):
    store = DynamoSessionStore(table, clock=clock)
    opening = turn("customer", "hola")
    save(store, 1, opening, opening, turn("assistant", "hola"))
    pk = session_pk(CUSTOMER_ID, SESSION)
    recorded = table.items[(pk, "SESSION")]["opening_sk"]
    assert recorded == turn_sk(opening, 1, 0)
    save(store, 2, opening, turn("customer", "otra"), turn("assistant", "otra"))  # opening not in this save
    assert table.items[(pk, "SESSION")]["opening_sk"] == recorded


def test_a_new_conversation_excludes_older_turns_even_with_the_same_timestamp(table, clock):
    """After an idle gap the window starts at the new opening turn, not at its timestamp, so turns of the
    previous conversation that share that timestamp stay out."""
    store = DynamoSessionStore(table, clock=clock)
    first = turn("customer", "conversación vieja")
    save(store, 1, first, first, turn("assistant", "respuesta vieja"))
    clock.advance(minutes=11)
    assert store.load(CUSTOMER_ID, SESSION) == SessionState(version=1)  # idle: a new conversation starts
    fresh = turn("customer", "conversación nueva")  # same TICK as the old turns
    save(store, 2, fresh, fresh, turn("assistant", "respuesta nueva"))

    assert [t.text for t in store.load(CUSTOMER_ID, SESSION).turns] == ["conversación nueva", "respuesta nueva"]
