"""scripts/handoffs.py against handoffs written by DynamoHandoffStore (no AWS)."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agents.conversations import DynamoHandoffStore
from agents.engines.escalation import Handoff
from agents.sessions import ConversationTurn, VerifiedFact

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "handoffs.py"
_spec = importlib.util.spec_from_file_location("handoffs_script", SCRIPT)
handoffs_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handoffs_script)

T0 = datetime(2026, 10, 5, 21, 0, tzinfo=UTC)


def _matches(condition, item) -> bool:
    """Just the conditions the script builds: AND, equality and begins_with on string attributes."""
    expression = condition.get_expression()
    operator, values = expression["operator"], expression["values"]
    if operator == "AND":
        return all(_matches(value, item) for value in values)
    attribute, operand = values[0].name, values[1]
    if operator == "=":
        return item.get(attribute) == operand
    if operator == "begins_with":
        return str(item.get(attribute, "")).startswith(operand)
    raise AssertionError(f"unexpected operator {operator}")


class FakeTable:
    """PutItem, plus Scan and Query that evaluate their conditions and return one item per page."""

    def __init__(self):
        self.items: list[dict] = []
        self.calls: list[str] = []

    def put_item(self, Item, ConditionExpression=None):
        self.items.append(dict(Item))

    def _page(self, condition, start):
        matching = [item for item in self.items if _matches(condition, item)]
        index = matching.index(start) + 1 if start else 0
        page = {"Items": matching[index : index + 1]}
        if index + 1 < len(matching):
            page["LastEvaluatedKey"] = matching[index]
        return page

    def scan(self, FilterExpression, ExclusiveStartKey=None):
        self.calls.append("scan")
        return self._page(FilterExpression, ExclusiveStartKey)

    def query(self, KeyConditionExpression, ConsistentRead=False, ExclusiveStartKey=None):
        self.calls.append("query")
        return self._page(KeyConditionExpression, ExclusiveStartKey)


def handoff(id_, minutes, customer="CUST-A", session="s1", status="pending", **fields) -> Handoff:
    return Handoff(
        id=id_,
        customer_id=customer,
        session_id=session,
        reason="human_requested",
        language="es",
        status=status,
        created_at=T0 + timedelta(minutes=minutes),
        open_question="Quiero hablar con un asesor",
        **fields,
    )


def stored(*handoffs: Handoff) -> FakeTable:
    table = FakeTable()
    table.put_item(Item={"PK": "CUST#CUST-A#SESSION#s1", "SK": "SESSION", "version": 3})
    store = DynamoHandoffStore(table)
    for item in handoffs:
        store.create(item)
    return table


def test_lists_pending_handoffs_newest_first_across_pages():
    table = stored(handoff("HO-OLD", 1), handoff("HO-NEW", 5, session="s2"), handoff("HO-DONE", 9))
    # Closed by a human workflow: it updates the item's status attribute, not the payload.
    table.items[-1]["status"] = "closed"

    found = handoffs_script.read_handoffs(table)

    assert [h.id for h in found] == ["HO-NEW", "HO-OLD"]
    assert table.calls.count("scan") > 1  # every page was read


def test_all_statuses_and_one_by_id():
    table = stored(handoff("HO-1", 1), handoff("HO-2", 2))

    table.items[-1]["status"] = "closed"

    assert [(h.id, h.status) for h in handoffs_script.read_handoffs(table, status=None)] == [
        ("HO-2", "closed"),
        ("HO-1", "pending"),
    ]
    assert [h.id for h in handoffs_script.read_handoffs(table, handoff_id="HO-1")] == ["HO-1"]


def test_one_session_is_a_query_on_its_partition():
    table = stored(handoff("HO-A", 1), handoff("HO-B", 2, customer="CUST-B"))

    found = handoffs_script.read_handoffs(table, customer_id="CUST-A", session_id="s1")

    assert [h.id for h in found] == ["HO-A"]
    assert table.calls == ["query"]


def test_render_reads_as_a_transcript():
    opening = ConversationTurn(role="customer", text="Hola, ¿cuál es mi ciudad?", at=T0)
    turns = (
        ConversationTurn(role="assistant", text="Vives en Cusco, Ana.", at=T0 + timedelta(seconds=5)),
        ConversationTurn(role="customer", text="Quiero hablar con un asesor", at=T0 + timedelta(seconds=9)),
    )
    fact = VerifiedFact(tool="get_my_profile", data={"first_name": "Ana", "city": "Cusco"}, retrieved_at=T0)

    text = handoffs_script.render(
        handoff("HO-X", 0, opening_message=opening, recent_turns=turns, verified_facts=(fact,))
    )

    assert "HO-X  [pending]  reason: human_requested  language: es" in text
    assert "Open question: Quiero hablar con un asesor" in text
    # The opening is shown on its own when it is no longer among the recent turns.
    assert "Conversation started with:\n  [2026-10-05 21:00:00] Hola, ¿cuál es mi ciudad?" in text
    assert "  [2026-10-05 21:00:05] Assistant: Vives en Cusco, Ana." in text
    assert "  get_my_profile (21:00:00): first_name=Ana, city=Cusco" in text
