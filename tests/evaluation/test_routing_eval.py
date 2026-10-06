"""Routing evaluation: does each message reach the right engine, with the right safety flags?

A case passes only if engine, language, credit_decision and sensitive_request all match.
Suites:
- routing/rules: RuleBasedRouter, offline.
- routing/hybrid: HybridRouter over Bedrock Haiku, what AGENT_ROUTER=bedrock runs (live).
- routing/model: the model's own answer inside that hybrid run, before the rules are OR-ed
  in; scored for the scorecard only, to show how much the deterministic checks carry.
"""

import pytest

from agents.context import AgentContext
from agents.routing import RouteResult, Router, RuleBasedRouter
from eval_kit import Pacer, Scorecard, case_params, load_cases

CASES = load_cases("routing.jsonl")


def context(case: dict) -> AgentContext:
    # The default language is the *other* one, so a pass shows the language was detected.
    default = "pt" if case["language"] == "es" else "es"
    return AgentContext(customer_id="EVAL-CUSTOMER", session_id=f"eval-{case['id']}", language=default)


def label(route: RouteResult) -> str:
    flags = [name for name in ("credit_decision", "sensitive_request") if getattr(route, name)]
    return "/".join([route.engine, route.language, *flags])


def expected_label(case: dict) -> str:
    flags = [name for name in ("credit_decision", "sensitive_request") if case.get(name, False)]
    return "/".join([case["engine"], case["language"], *flags])


def evaluate(scorecard: Scorecard, suite: str, router: Router, case: dict) -> None:
    route = router.route(context(case), case["message"])
    expected, actual = expected_label(case), label(route)
    scorecard.record(suite, case, expected, actual, confidence=route.confidence)
    assert actual == expected, f"{case['message']!r}: expected {expected}, got {actual}"


@pytest.mark.parametrize("case", case_params(CASES, "rules"))
def test_rule_router(case, scorecard):
    evaluate(scorecard, "routing/rules", RuleBasedRouter(), case)


class RecordingRouter:
    """Passes through to the model router, paced, and keeps its last raw answer."""

    def __init__(self, inner: Router, pacer: Pacer):
        self.inner = inner
        self.pacer = pacer
        self.last: RouteResult | None = None

    def route(self, context: AgentContext, message: str) -> RouteResult:
        self.pacer.wait()
        self.last = self.inner.route(context, message)
        return self.last


@pytest.fixture(scope="module")
def hybrid(bedrock_config, bedrock_client, model_pacer):
    from pydantic_ai import models

    from agents.model_router import HybridRouter, ModelRouter
    from agents.models import bedrock_router_model

    models.ALLOW_MODEL_REQUESTS = True
    raw = RecordingRouter(ModelRouter(bedrock_router_model(bedrock_config, bedrock_client)), model_pacer)
    return HybridRouter(raw), raw


@pytest.mark.live
@pytest.mark.parametrize("case", case_params(CASES, "hybrid"))
def test_hybrid_router(case, scorecard, hybrid):
    router, raw = hybrid
    raw.last = None
    route = router.route(context(case), case["message"])
    expected = expected_label(case)
    if raw.last is None:
        # The model call failed (e.g. throttled) and HybridRouter fell back to the rules, so this
        # case says nothing about the model: score it as an error, not as the rules' answer.
        scorecard.record("routing/hybrid", case, expected, "model_error")
        scorecard.record("routing/model", case, expected, "model_error")
        pytest.fail("the model call failed and the rules answered instead; see the log (throttling?)")
    actual = label(route)
    scorecard.record("routing/hybrid", case, expected, actual, confidence=route.confidence)
    scorecard.record("routing/model", case, expected, label(raw.last))
    assert actual == expected, f"{case['message']!r}: expected {expected}, got {actual}"
