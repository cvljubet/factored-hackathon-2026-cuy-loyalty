"""Chat sessions, turns and handoffs in the DynamoDB conversations table (CONVERSATIONS_BACKEND=dynamodb).

All items of one session share a partition:

    PK = CUST#<customer_id>#SESSION#<session_id>
    SK = SESSION                     version, language, failure count, opening message, facts, handoff state
    SK = TURN#<ts>#<message_id>      one customer or assistant message (text already redacted)
    SK = HANDOFF#<ts>#<handoff_id>   one escalation: the Handoff as JSON in payload

<ts> is UTC ISO 8601 with microseconds and a fixed width, so SK order is time order. Every item has
expires_at (epoch seconds) for the table's TTL. Facts and handoffs are JSON strings: tool results hold
floats, which the boto3 resource layer refuses.

The customer_id is always the verified one the orchestrator was given; it is part of every key, so a
session id never reaches another customer's items. Nothing here stores tokens, claims or guardrail
details: only the redacted conversation, tool results the assistant could show, and counters.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from pydantic import TypeAdapter

from agents.engines.escalation import Handoff
from agents.sessions import MAX_TURNS, ConversationTurn, SessionConflict, SessionState, VerifiedFact

SESSION_SK = "SESSION"
TURN_PREFIX = "TURN#"
HANDOFF_PREFIX = "HANDOFF#"
# Sorts after every TURN#<ts>, so BETWEEN TURN#<from> AND this reads every turn from <from> on.
TURN_END = "TURN#~"

_facts = TypeAdapter(tuple[VerifiedFact, ...])


def _now() -> datetime:
    return datetime.now(UTC)


def session_pk(customer_id: str, session_id: str) -> str:
    """CUST#<customer_id>#SESSION#<session_id>. A separator in either id is refused, so no id can name
    another customer's or session's partition."""
    for name, value in (("customer_id", customer_id), ("session_id", session_id)):
        if not value or "#" in value:
            raise ValueError(f"{name} must be non-empty and must not contain '#'")
    return f"CUST#{customer_id}#SESSION#{session_id}"


def sort_time(at: datetime) -> str:
    """Fixed-width UTC time for sort keys: 2026-10-05T21:30:00.123456Z."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _epoch(at: datetime) -> int:
    return int(at.timestamp())


def _conflict(error: ClientError) -> bool:
    """A failed condition (another save won) or a transaction clash on the same item."""
    if error.response.get("Error", {}).get("Code") != "TransactionCanceledException":
        return False
    reasons = {reason.get("Code") for reason in error.response.get("CancellationReasons", [])}
    message = error.response.get("Error", {}).get("Message", "")
    return bool(reasons & {"ConditionalCheckFailed", "TransactionConflict"}) or "ConditionalCheckFailed" in message


class DynamoSessionStore:
    """SessionStore over the conversations table.

    load: GetItem on SESSION, then a Query for the latest MAX_TURNS turns of the current conversation
    (from its opening message on), newest first. A session idle for longer than idle_minutes loads as
    a new conversation (same version, so the next save still checks it).
    save: one transaction that updates SESSION only if its version is still the loaded one, and adds
    this turn's TURN# items.
    """

    def __init__(
        self,
        table: Any,
        *,
        idle_minutes: int = 10,
        retention_days: int = 7,
        clock: Callable[[], datetime] = _now,
    ):
        self.table = table
        self.idle = timedelta(minutes=idle_minutes)
        self.retention = timedelta(days=retention_days)
        self.clock = clock

    def load(self, customer_id: str, session_id: str) -> SessionState:
        pk = session_pk(customer_id, session_id)
        # Consistent reads, so a quick second message sees the version the first one saved.
        item = self.table.get_item(Key={"PK": pk, "SK": SESSION_SK}, ConsistentRead=True).get("Item")
        if item is None:
            return SessionState()
        version = int(item["version"])
        if self.clock() - datetime.fromisoformat(item["updated_at"]) > self.idle:
            return SessionState(version=version)
        opening = ConversationTurn.model_validate_json(item["opening"]) if item.get("opening") else None
        return SessionState(
            version=version,
            consecutive_failures=int(item.get("consecutive_failures", 0)),
            language=item.get("language"),
            turns=self._recent_turns(pk, opening),
            facts=_facts.validate_json(item.get("facts", "[]")),
            opening=opening,
            handoff_count=int(item.get("handoff_count", 0)),
            last_handoff_id=item.get("last_handoff_id"),
            last_handoff_reason=item.get("last_handoff_reason"),
        )

    def _recent_turns(self, pk: str, opening: ConversationTurn | None) -> tuple[ConversationTurn, ...]:
        """The latest MAX_TURNS turns since the opening message (older ones belong to an expired
        conversation in the same session), oldest first."""
        start = f"{TURN_PREFIX}{sort_time(opening.at)}" if opening else TURN_PREFIX
        page = self.table.query(
            KeyConditionExpression=Key("PK").eq(pk) & Key("SK").between(start, TURN_END),
            ScanIndexForward=False,
            Limit=MAX_TURNS,
            ConsistentRead=True,
        )
        turns = [
            ConversationTurn(role=item["role"], text=item["text"], at=item["at"], message_id=item["message_id"])
            for item in page.get("Items", [])
        ]
        return tuple(reversed(turns))

    def save(
        self, customer_id: str, session_id: str, state: SessionState, new_turns: tuple[ConversationTurn, ...]
    ) -> None:
        pk = session_pk(customer_id, session_id)
        now = self.clock()
        expires_at = _epoch(now + self.retention)
        values: dict[str, Any] = {
            "version": state.version,
            "consecutive_failures": state.consecutive_failures,
            "language": state.language,
            "opening": state.opening.model_dump_json() if state.opening else None,
            "facts": _facts.dump_json(state.facts).decode(),
            "handoff_count": state.handoff_count,
            "last_handoff_id": state.last_handoff_id,
            "last_handoff_reason": state.last_handoff_reason,
            "updated_at": now.isoformat(),
            "expires_at": expires_at,
        }
        present = {name: value for name, value in values.items() if value is not None}
        absent = [name for name, value in values.items() if value is None]
        # Placeholders for every name, since some (e.g. language) may be DynamoDB reserved words.
        sets = [f"#{name} = :{name}" for name in present] + ["#created_at = if_not_exists(#created_at, :now)"]
        expression = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(f"#{n}" for n in absent) if absent else "")
        session_update = {
            "Update": {
                "TableName": self.table.name,
                "Key": {"PK": pk, "SK": SESSION_SK},
                "UpdateExpression": expression,
                "ConditionExpression": "attribute_not_exists(PK) OR #version = :previous",
                "ExpressionAttributeNames": {f"#{name}": name for name in [*values, "created_at"]},
                "ExpressionAttributeValues": {
                    **{f":{name}": value for name, value in present.items()},
                    ":now": now.isoformat(),
                    ":previous": state.version - 1,
                },
            }
        }
        turn_puts = [
            {
                "Put": {
                    "TableName": self.table.name,
                    "Item": {
                        "PK": pk,
                        "SK": f"{TURN_PREFIX}{sort_time(turn.at)}#{turn.message_id}",
                        "role": turn.role,
                        "text": turn.text,
                        "at": turn.at.isoformat(),
                        "message_id": turn.message_id,
                        "expires_at": expires_at,
                    },
                    "ConditionExpression": "attribute_not_exists(SK)",
                }
            }
            for turn in new_turns
        ]
        try:
            self.table.meta.client.transact_write_items(TransactItems=[session_update, *turn_puts])
        except ClientError as error:
            if _conflict(error):
                raise SessionConflict(f"session {session_id} was saved since it was loaded") from error
            raise


class DynamoHandoffStore:
    """HandoffStore over the conversations table: one HANDOFF# item in the session's partition, kept
    retention_days after it was created (longer than the session, so a human can still work on it)."""

    def __init__(self, table: Any, *, retention_days: int = 30):
        self.table = table
        self.retention = timedelta(days=retention_days)

    def create(self, handoff: Handoff) -> None:
        self.table.put_item(
            Item={
                "PK": session_pk(handoff.customer_id, handoff.session_id),
                "SK": f"{HANDOFF_PREFIX}{sort_time(handoff.created_at)}#{handoff.id}",
                "handoff_id": handoff.id,
                "reason": handoff.reason,
                "status": handoff.status,
                "language": handoff.language,
                "created_at": handoff.created_at.isoformat(),
                "payload": handoff.model_dump_json(),
                "expires_at": _epoch(handoff.created_at + self.retention),
            },
            ConditionExpression="attribute_not_exists(SK)",
        )
