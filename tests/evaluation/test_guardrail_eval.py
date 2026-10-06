"""Guardrail evaluation: is each text blocked or allowed as intended, and by the intended policy?

Each case gives the desired outcome ("expect": block/allow) for a customer message (INPUT)
or an assistant reply (OUTPUT). Suites:
- guardrail/scan: the deterministic scan_output, for OUTPUT cases with a "scan" label (offline).
- guardrail/bedrock: the published Bedrock guardrail through BedrockGuardrail (live). A case
  with a "policy" label (e.g. "topic:datos_de_otros_clientes") also checks what intervened.
- guardrail/turn: whole orchestrator turns with that guardrail and the rule router (live), so
  the layers are scored together: a blocked input must never reach the router, and a reply
  must be blocked if either the scan or the guardrail objects. An allowed INPUT case with an
  "engine" label must also reach that engine (e.g. a human request must hand off, not just pass).
"""

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.factory import build_orchestrator
from agents.guardrails import GUARDRAIL_UNAVAILABLE
from agents.safety import scan_output
from agents.serving import InMemoryServingRepository
from eval_kit import case_params, intervened_policies, load_cases

CASES = load_cases("guardrail.jsonl")
SCAN_CASES = [case for case in CASES if "scan" in case]
CONTEXT_CUSTOMER = "EVAL-CUSTOMER"


def outcome(allowed: bool) -> str:
    return "allow" if allowed else "block"


@pytest.mark.parametrize("case", case_params(SCAN_CASES, "scan"))
def test_output_scan(case, scorecard):
    actual = outcome(scan_output(case["text"]).allowed)
    scorecard.record("guardrail/scan", case, case["scan"], actual)
    assert actual == case["scan"]


def eval_context(case: dict):
    from agents.context import AgentContext

    return AgentContext(customer_id=CONTEXT_CUSTOMER, session_id=f"eval-{case['id']}", language="es")


@pytest.mark.live
@pytest.mark.parametrize("case", case_params(CASES, "bedrock"))
def test_bedrock_guardrail(case, scorecard, guardrail, recording_client):
    check = guardrail.check_input if case["source"] == "INPUT" else guardrail.check_output
    verdict = check(eval_context(case), case["text"])
    # Fail closed would otherwise score an unreachable guardrail as a perfect blocker.
    assert verdict.reason != GUARDRAIL_UNAVAILABLE, "ApplyGuardrail failed; check credentials, ID and version"

    policies = intervened_policies(recording_client.last or {})
    actual = outcome(verdict.allowed)
    scorecard.record("guardrail/bedrock", case, case["expect"], actual, source=case["source"], policies=policies)
    assert actual == case["expect"], f"{case['source']} {case['text']!r}: intervened={policies}"
    if case["expect"] == "block" and "policy" in case:
        assert case["policy"] in policies, f"blocked, but by {policies} instead of {case['policy']}"


# A question the rule router sends to the inquiry engine, so OUTPUT cases reach output screening.
INQUIRY_QUESTION = "¿Cuál es el saldo de mi cuenta?"


def canned_reply(text: str) -> FunctionModel:
    """An inquiry model that answers with the case's text, standing in for an LLM that said it."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond, model_name="canned")


@pytest.mark.live
@pytest.mark.parametrize("case", case_params(CASES, "turn"))
def test_turn(case, scorecard, guardrail):
    is_input = case["source"] == "INPUT"
    orchestrator = build_orchestrator(
        serving=InMemoryServingRepository({}),
        model=canned_reply("Tienes dos productos activos." if is_input else case["text"]),
        guardrail=guardrail,
    )
    reply = orchestrator.handle(
        customer_id=CONTEXT_CUSTOMER,
        session_id=f"eval-{case['id']}",
        user_message=case["text"] if is_input else INQUIRY_QUESTION,
    )
    trace = reply.trace
    if is_input:
        blocked = trace.blocked_reason == "bedrock_guardrail_input"
        if blocked:
            assert trace.route is None, "a blocked input reached the router"
    else:
        blocked = reply.status == "blocked"
    expected, actual = case["expect"], outcome(not blocked)
    if is_input and "engine" in case:
        expected = f"{expected}/{case['engine']}" if expected == "allow" else expected
        actual = f"{actual}/{reply.engine}" if not blocked else actual
    scorecard.record(
        "guardrail/turn", case, expected, actual, engine=reply.engine, blocked_reason=trace.blocked_reason
    )
    assert actual == expected, f"engine={reply.engine} status={reply.status} reason={trace.blocked_reason}"
