"""BedrockGuardrail against a stubbed bedrock-runtime client (botocore Stubber, no AWS request)."""

import boto3
import pytest
from botocore.stub import Stubber

from agents.guardrails import ALLOWED, GUARDRAIL_UNAVAILABLE, BedrockGuardrail, GuardrailVerdict
from agent_testkit import make_context

GUARDRAIL_ID = "gr-test123"
VERSION = "1"


def guardrail_response(action: str) -> dict:
    usage = {
        "topicPolicyUnits": 0,
        "contentPolicyUnits": 1,
        "wordPolicyUnits": 0,
        "sensitiveInformationPolicyUnits": 1,
        "sensitiveInformationPolicyFreeUnits": 0,
        "contextualGroundingPolicyUnits": 0,
    }
    return {"usage": usage, "action": action, "outputs": [], "assessments": []}


def expected_params(source: str, text: str) -> dict:
    return {
        "guardrailIdentifier": GUARDRAIL_ID,
        "guardrailVersion": VERSION,
        "source": source,
        "content": [{"text": {"text": text}}],
        "outputScope": "INTERVENTIONS",
    }


@pytest.fixture
def stubbed():
    client = boto3.client("bedrock-runtime", region_name="us-east-2")
    with Stubber(client) as stubber:
        yield BedrockGuardrail(client, GUARDRAIL_ID, VERSION), stubber
        stubber.assert_no_pending_responses()


def test_input_is_checked_as_input_and_allowed_when_nothing_intervenes(stubbed):
    guardrail, stubber = stubbed
    stubber.add_response("apply_guardrail", guardrail_response("NONE"), expected_params("INPUT", "¿Mi saldo?"))

    assert guardrail.check_input(make_context(), "¿Mi saldo?") == ALLOWED


def test_output_is_checked_as_output_and_blocked_when_the_guardrail_intervenes(stubbed):
    guardrail, stubber = stubbed
    stubber.add_response(
        "apply_guardrail", guardrail_response("GUARDRAIL_INTERVENED"), expected_params("OUTPUT", "respuesta")
    )

    assert guardrail.check_output(make_context(), "respuesta") == GuardrailVerdict(False, "bedrock_guardrail_output")


def test_an_intervention_on_input_is_not_an_outage(stubbed):
    guardrail, stubber = stubbed
    stubber.add_response("apply_guardrail", guardrail_response("GUARDRAIL_INTERVENED"), expected_params("INPUT", "x"))

    verdict = guardrail.check_input(make_context(), "x")

    assert (verdict.allowed, verdict.reason) == (False, "bedrock_guardrail_input")


@pytest.mark.parametrize("check", ["check_input", "check_output"])
def test_a_failed_call_blocks_the_text(stubbed, check):
    guardrail, stubber = stubbed
    stubber.add_client_error("apply_guardrail", service_error_code="ThrottlingException", http_status_code=429)

    verdict = getattr(guardrail, check)(make_context(), "hola")

    assert verdict == GuardrailVerdict(False, GUARDRAIL_UNAVAILABLE)
