import json
import logging

import pytest

from agents.orchestrator import AgentReply, TurnTrace
from agents.routing import RouteResult
from app.observability import TURN_LOGGER, configure_logging, log_turn, turn_record


def reply(**trace) -> AgentReply:
    return AgentReply(
        session_id="session-123",
        reply="Tu saldo es S/ 100.",
        engine="inquiry",
        language="es",
        status="answered",
        trace=TurnTrace(
            route=RouteResult(engine="inquiry", language="es", confidence=0.9),
            tools_called=("get_my_profile",),
            model_requests=2,
            input_tokens=900,
            output_tokens=40,
            models_used=("us.anthropic.claude-haiku-4-5-20251001-v1:0",),
            stage_ms={"guardrail_input": 50.0, "router": 400.0, "engine": 1500.0, "guardrail_output": 60.0},
            **trace,
        ),
    )


class CapturedTurns(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def captured_turns():
    """The JSON lines written to the per-turn logger (it does not propagate, so caplog misses them)."""
    handler = CapturedTurns()
    logger = logging.getLogger(TURN_LOGGER)
    logger.addHandler(handler)
    yield handler
    logger.removeHandler(handler)


def test_turn_record_describes_the_turn_without_its_text():
    record = turn_record(reply(), latency_ms=2034.56)

    assert record["event"] == "chat_turn"
    assert (record["engine"], record["status"], record["failed"], record["escalated"]) == (
        "inquiry",
        "answered",
        False,
        False,
    )
    assert (record["model_requests"], record["input_tokens"], record["output_tokens"]) == (2, 900, 40)
    assert record["latency_ms"] == 2034.6
    assert record["stage_ms"]["engine"] == 1500.0
    assert record["session"] != "session-123" and len(record["session"]) == 12
    assert "Tu saldo" not in json.dumps(record)


def test_a_turn_that_failed_is_flagged():
    assert turn_record(reply(consecutive_failures=1), 10)["failed"] is True


def test_log_turn_writes_one_json_object(captured_turns):
    log_turn(reply(), 12.0)

    [line] = captured_turns.lines
    assert json.loads(line)["event"] == "chat_turn"


def test_configure_logging_adds_its_handlers_once():
    configure_logging("INFO")
    configure_logging("WARNING")

    assert sum(getattr(h, "_cuy_loyalty_handler", False) for h in logging.getLogger().handlers) == 1
    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger(TURN_LOGGER).propagate is False
    configure_logging("INFO")

