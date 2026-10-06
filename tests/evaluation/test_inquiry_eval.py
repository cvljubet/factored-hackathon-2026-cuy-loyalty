"""Inquiry evaluation: the inquiry agent on Bedrock, one question per case, over fixed synthetic data.

Each case in datasets/inquiry.jsonl is a question from a customer of datasets/inquiry_serving.json;
inquiry_kit.py runs its turn and scores it with checks that need no judge model. Suites, one per
check in order of value: inquiry/tools, inquiry/grounded, inquiry/scoped, inquiry/language,
inquiry/screened, inquiry/budget; and inquiry/turn, which passes when every check does and carries
the turn's requests, tokens, latency, answering model and estimated cost for the report
(evals/compare.py compares them between runs).

Live only. The model is the one the backend builds from the same variables: BEDROCK_INQUIRY_MODEL_ID
(default Haiku 4.5) with BEDROCK_INQUIRY_FALLBACK_MODEL_ID (e.g. Sonnet 4.6) when set. Every model
request waits for the shared pacer (EVAL_MODEL_RPM); the suite makes about 40 of them, about four minutes.
"""

import pytest

from eval_kit import MeteredClient, case_params, load_cases
from inquiry_kit import CHECKS, expected_labels, load_serving_data, run_turn, score, screen

CASES = load_cases("inquiry.jsonl")
SERVING_DATA = load_serving_data()


@pytest.fixture(scope="module")
def meter(bedrock_client, model_pacer):
    return MeteredClient(bedrock_client, model_pacer)


@pytest.fixture(scope="module")
def inquiry_model(bedrock_config, meter):
    from pydantic_ai import models

    from agents.models import bedrock_inquiry_model

    models.ALLOW_MODEL_REQUESTS = True
    return bedrock_inquiry_model(bedrock_config, meter)


def record_error(scorecard, case: dict, error: str, **detail) -> None:
    """A turn that measured nothing about the agent: every suite records the error, so compare.py
    counts the case apart instead of as a failure."""
    expected = expected_labels(case)
    for check in CHECKS:
        scorecard.record(f"inquiry/{check}", case, expected[check], error, tags=[])
    scorecard.record("inquiry/turn", case, expected["turn"], error, **detail)


@pytest.mark.live
@pytest.mark.parametrize("case", case_params(CASES, "inquiry"))
def test_inquiry(case, scorecard, inquiry_model, meter, guardrail, recording_client):
    outcome = run_turn(case, inquiry_model, SERVING_DATA, meter)
    if outcome.model_error:
        record_error(scorecard, case, "model_error", cause=outcome.model_error, metrics=outcome.metrics())
        pytest.fail(f"the model call failed ({outcome.model_error}); see the log (throttling?)")
    reply = outcome.result.reply
    screening = screen(case, reply, guardrail, recording_client)
    if screening.unavailable:
        record_error(scorecard, case, "guardrail_error", reply=reply, metrics=outcome.metrics())
        pytest.fail("ApplyGuardrail failed; check credentials, ID and version")

    checks = score(case, outcome, screening)
    for check in checks:
        scorecard.record(
            f"inquiry/{check.name}", case, check.expected, check.actual, passed=check.passed, tags=[], **check.detail
        )
    failed = [check for check in checks if not check.passed]
    scorecard.record(
        "inquiry/turn",
        case,
        expected_labels(case)["turn"],
        f"fail: {', '.join(check.name for check in failed)}" if failed else "pass",
        reply=reply,
        tools_called=list(outcome.result.tools_called),
        lookups=outcome.lookups,
        metrics=outcome.metrics(),
    )
    assert not failed, "; ".join(f"{check.name}: {check.actual}" for check in failed) + f"\nreply: {reply!r}"
