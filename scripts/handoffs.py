"""Read handoffs from the conversations table and print them as transcripts for a human agent.

Read-only. Uses the same CONVERSATIONS_* settings as the backend (environment or the repo-root .env):
CONVERSATIONS_TABLE_NAME, CONVERSATIONS_AWS_REGION and CONVERSATIONS_AWS_PROFILE (unset: the default chain).

    uv run python scripts/handoffs.py                                   # pending handoffs, newest first
    uv run python scripts/handoffs.py --all --limit 5                   # any status
    uv run python scripts/handoffs.py --id HO-1A2B3C4D5E                # one handoff
    uv run python scripts/handoffs.py --customer CUST-1 --session abc   # one session (a query, not a scan)
    uv run python scripts/handoffs.py --json | jq .                     # the stored payloads

The table has no secondary index, so every form but --customer/--session scans the whole table:
fine for the hackathon's volumes, not for production.
"""

import argparse
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]  # the same import paths pytest uses

from boto3.dynamodb.conditions import Attr, Key  # noqa: E402

from agents.conversations import HANDOFF_PREFIX, session_pk  # noqa: E402
from agents.engines.escalation import Handoff  # noqa: E402
from agents.serving import dynamodb_serving_table  # noqa: E402
from app.config import Settings  # noqa: E402

ROLES = {"customer": "Customer", "assistant": "Assistant"}


class StoredHandoff(Handoff):
    """A stored handoff with any status: the backend only writes "pending", a human workflow may change it."""

    status: str


def _pages(call: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Every item of a paginated scan or query."""
    while True:
        page = call(**kwargs)
        yield from page.get("Items", [])
        if "LastEvaluatedKey" not in page:
            return
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def read_handoffs(
    table: Any,
    *,
    handoff_id: str | None = None,
    customer_id: str | None = None,
    session_id: str | None = None,
    status: str | None = "pending",
) -> list[StoredHandoff]:
    """Matching handoffs, newest first."""
    if customer_id and session_id:
        condition = Key("PK").eq(session_pk(customer_id, session_id)) & Key("SK").begins_with(HANDOFF_PREFIX)
        items = _pages(table.query, KeyConditionExpression=condition, ConsistentRead=True)
    else:
        condition = Attr("SK").begins_with(HANDOFF_PREFIX)
        if handoff_id:
            condition &= Attr("handoff_id").eq(handoff_id)
        items = _pages(table.scan, FilterExpression=condition)
    # The item's own status attribute is the current one; the payload keeps the status at creation.
    handoffs = [
        StoredHandoff.model_validate_json(item["payload"]).model_copy(update={"status": item.get("status", "pending")})
        for item in items
    ]
    if handoff_id:
        handoffs = [h for h in handoffs if h.id == handoff_id]
    elif status:
        handoffs = [h for h in handoffs if h.status == status]
    return sorted(handoffs, key=lambda h: h.created_at, reverse=True)


def _time(handoff_turn: Any) -> str:
    return handoff_turn.at.strftime("%Y-%m-%d %H:%M:%S")


def render(handoff: Handoff) -> str:
    """One handoff as plain text: why, what is still open, the conversation and what the assistant knew."""
    lines = [
        f"{handoff.id}  [{handoff.status}]  reason: {handoff.reason}  language: {handoff.language}",
        (
            f"Created {handoff.created_at:%Y-%m-%d %H:%M:%S} UTC  customer {handoff.customer_id}  "
            f"session {handoff.session_id}"
        ),
        f"Open question: {handoff.open_question}",
    ]
    turn_ids = {turn.message_id for turn in handoff.recent_turns}
    if handoff.opening_message and handoff.opening_message.message_id not in turn_ids:
        opening = handoff.opening_message
        lines += ["", "Conversation started with:", f"  [{_time(opening)}] {opening.text}"]
    lines += ["", "Conversation:"]
    lines += [f"  [{_time(turn)}] {ROLES[turn.role]}: {turn.text}" for turn in handoff.recent_turns] or ["  (none)"]
    lines += ["", "What the assistant had retrieved:"]
    for fact in handoff.verified_facts:
        values = ", ".join(
            f"{key}={json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value}"
            for key, value in fact.data.items()
        )
        lines.append(f"  {fact.tool} ({fact.retrieved_at:%H:%M:%S}): {values or '(empty)'}")
    if not handoff.verified_facts:
        lines.append("  (nothing)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--id", dest="handoff_id", help="one handoff, e.g. HO-1A2B3C4D5E")
    parser.add_argument("--customer", help="the customer_id (with --session: read that session only)")
    parser.add_argument("--session", help="the session_id returned by POST /chat")
    parser.add_argument("--all", action="store_true", help="any status, not only pending")
    parser.add_argument("--limit", type=int, default=20, help="at most this many, newest first (default 20)")
    parser.add_argument("--json", action="store_true", help="print the stored payloads as JSON lines")
    parser.add_argument("--table", help="table name (default: CONVERSATIONS_TABLE_NAME)")
    parser.add_argument("--profile", help="AWS profile (default: CONVERSATIONS_AWS_PROFILE, else the default chain)")
    args = parser.parse_args()
    if bool(args.customer) != bool(args.session):
        parser.error("--customer and --session go together")

    settings = Settings(cognito_region="-", cognito_user_pool_id="-", cognito_app_client_id="-")
    table_name = args.table or settings.conversations_table_name
    table = dynamodb_serving_table(
        table_name, settings.conversations_aws_region, args.profile or settings.conversations_aws_profile
    )
    handoffs = read_handoffs(
        table,
        handoff_id=args.handoff_id,
        customer_id=args.customer,
        session_id=args.session,
        status=None if args.all else "pending",
    )[: args.limit]

    if args.json:
        for handoff in handoffs:
            print(handoff.model_dump_json())
        return 0
    if not handoffs:
        print(f"No matching handoffs in {table_name}.", file=sys.stderr)
        return 1
    print(f"\n\n{'=' * 100}\n\n".join(render(handoff) for handoff in handoffs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
