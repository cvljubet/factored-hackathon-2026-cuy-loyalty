"""Application logging and the structured per-chat-turn record.

uvicorn configures only its own loggers, so without configure_logging the agents' and the
app's records (INFO and below) never reach the container's output, and with it CloudWatch.

Each chat turn writes one JSON line (event "chat_turn") through its own logger, with nothing
else on the line, so CloudWatch Logs Insights reads its fields and the log metric filters in
infrastructure/terraform/envs/dev/observability.tf turn them into metrics. The line never
holds message text or the customer_id: Bedrock's invocation logging in the model account
keeps prompts and replies, and the session appears only as a hash.
"""

import hashlib
import json
import logging
import sys
from typing import Any

from agents.orchestrator import AgentReply

TURN_LOGGER = "app.turns"
_HANDLER_FLAG = "_cuy_loyalty_handler"


def configure_logging(level: str = "INFO") -> None:
    """Send application records to stdout; calling it again only changes the level."""
    root = logging.getLogger()
    if not any(getattr(handler, _HANDLER_FLAG, False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        setattr(handler, _HANDLER_FLAG, True)
        root.addHandler(handler)
    root.setLevel(level)

    turns = logging.getLogger(TURN_LOGGER)
    if not any(getattr(handler, _HANDLER_FLAG, False) for handler in turns.handlers):
        # The bare JSON, with no level or logger prefix, so each line parses as one JSON object.
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        setattr(handler, _HANDLER_FLAG, True)
        turns.addHandler(handler)
    turns.setLevel(logging.INFO)
    turns.propagate = False


def turn_record(reply: AgentReply, latency_ms: float) -> dict[str, Any]:
    """The per-turn record: how the turn was handled, never what was said."""
    trace = reply.trace
    route = trace.route
    return {
        "event": "chat_turn",
        "session": hashlib.sha256(reply.session_id.encode()).hexdigest()[:12],
        "engine": reply.engine,
        "status": reply.status,
        "language": reply.language,
        "failed": trace.failed,
        "consecutive_failures": trace.consecutive_failures,
        "escalated": reply.escalated,
        "handoff_id": reply.handoff_id,
        "blocked_reason": trace.blocked_reason,
        "route_engine": route.engine if route else None,
        "route_confidence": route.confidence if route else None,
        "credit_decision": route.credit_decision if route else False,
        "sensitive_request": route.sensitive_request if route else False,
        "tools": list(trace.tools_called),
        "model_requests": trace.model_requests,
        "input_tokens": trace.input_tokens,
        "output_tokens": trace.output_tokens,
        "models": list(trace.models_used),
        "fallback_used": trace.fallback_used,
        "latency_ms": round(latency_ms, 1),
        "stage_ms": trace.stage_ms,
    }


def log_turn(reply: AgentReply, latency_ms: float) -> None:
    logging.getLogger(TURN_LOGGER).info(json.dumps(turn_record(reply, latency_ms), ensure_ascii=False))
