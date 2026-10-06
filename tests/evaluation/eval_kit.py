"""Shared helpers for the evaluation suites: datasets, parametrisation, the scorecard, and the
wrappers around the bedrock-runtime client (pacing, metering, guardrail responses)."""

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

DATASETS = Path(__file__).parent / "datasets"


def load_cases(name: str) -> list[dict[str, Any]]:
    lines = (DATASETS / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def case_params(cases: list[dict[str, Any]], suite: str) -> list[Any]:
    """One pytest param per case; a case's known_gap for this suite turns it into a non-strict xfail."""
    params = []
    for case in cases:
        gap = case.get("known_gap", {}).get(suite)
        marks = [pytest.mark.xfail(reason=gap, strict=False)] if gap else []
        params.append(pytest.param(case, id=case["id"], marks=marks))
    return params


@dataclass
class CaseResult:
    suite: str
    case_id: str
    passed: bool
    expected: str
    actual: str
    tags: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


class Scorecard:
    def __init__(self) -> None:
        self.results: list[CaseResult] = []

    def record(
        self,
        suite: str,
        case: dict[str, Any],
        expected: str,
        actual: str,
        *,
        passed: bool | None = None,
        tags: list[str] | None = None,
        **detail: Any,
    ) -> bool:
        """passed defaults to expected == actual; tags default to the case's."""
        if passed is None:
            passed = expected == actual
        tags = case.get("tags", []) if tags is None else tags
        self.results.append(CaseResult(suite, case["id"], passed, expected, actual, tags, detail))
        return passed

    def lines(self) -> list[str]:
        by_suite: dict[str, list[CaseResult]] = defaultdict(list)
        for result in self.results:
            by_suite[result.suite].append(result)
        out = []
        for suite, results in by_suite.items():
            passed = sum(r.passed for r in results)
            out.append(f"{suite}: {passed}/{len(results)} ({passed / len(results):.0%})")
            for label, subset in _breakdown(results).items():
                ok = sum(r.passed for r in subset)
                out.append(f"    {label:<40} {ok}/{len(subset)}")
            for r in results:
                if not r.passed:
                    out.append(f"    MISS {r.case_id}: expected {r.expected}, got {r.actual}")
        return out


def _breakdown(results: list[CaseResult]) -> dict[str, list[CaseResult]]:
    """Per expected label (e.g. engine, or block/allow), unless the suite has only one, and per tag."""
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    by_label = len({r.expected for r in results}) > 1
    for r in results:
        if by_label:
            groups[f"expected={r.expected}"].append(r)
        for tag in r.tags:
            groups[f"tag={tag}"].append(r)
    return dict(sorted(groups.items()))


class Pacer:
    """Spaces calls to at most `per_minute` a minute, so a run stays under the account's quota."""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


class RecordingClient:
    """The real bedrock-runtime client, keeping the last ApplyGuardrail response."""

    def __init__(self, client: Any):
        self.client = client
        self.last: dict[str, Any] | None = None

    def apply_guardrail(self, **kwargs: Any) -> dict[str, Any]:
        self.last = None
        self.last = self.client.apply_guardrail(**kwargs)
        return self.last


def intervened_policies(response: dict[str, Any]) -> list[str]:
    """What blocked, as "topic:<name>", "content:<type>", "pii:<type>"... (never the matched text)."""
    found = []
    for assessment in response.get("assessments", []):
        for topic in assessment.get("topicPolicy", {}).get("topics", []):
            if topic.get("action") == "BLOCKED":
                found.append(f"topic:{topic['name']}")
        for content in assessment.get("contentPolicy", {}).get("filters", []):
            if content.get("action") == "BLOCKED":
                found.append(f"content:{content['type']}")
        sensitive = assessment.get("sensitiveInformationPolicy", {})
        for pii in sensitive.get("piiEntities", []):
            if pii.get("action") == "BLOCKED":
                found.append(f"pii:{pii['type']}")
        for regex in sensitive.get("regexes", []):
            if regex.get("action") == "BLOCKED":
                found.append(f"regex:{regex['name']}")
        for word in assessment.get("wordPolicy", {}).get("customWords", []):
            if word.get("action") == "BLOCKED":
                found.append("word:custom")
    return sorted(set(found))


@dataclass
class ModelCall:
    """One Converse request, as Bedrock billed and throttled it."""

    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    # Botocore's own retries inside this call (throttling, timeouts).
    retries: int = 0
    error: str | None = None


class MeteredClient:
    """The bedrock-runtime client with every Converse call (one model request) paced and recorded.

    Pydantic AI's BedrockConverseModel makes one client.converse call per request, so this sees
    what Bedrock bills and throttles, a fallback model's calls and failed calls included.
    Everything else (meta, apply_guardrail...) goes straight to the real client.
    """

    def __init__(self, client: Any, pacer: Pacer):
        self._client = client
        self._pacer = pacer
        self.calls: list[ModelCall] = []
        self.paced_seconds = 0.0

    def reset(self) -> None:
        self.calls = []
        self.paced_seconds = 0.0

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        start = time.perf_counter()
        self._pacer.wait()
        self.paced_seconds += time.perf_counter() - start
        call = ModelCall(model_id=str(kwargs.get("modelId")))
        self.calls.append(call)
        try:
            response = self._client.converse(**kwargs)
        except Exception as error:
            # A ClientError carries the parsed error response (e.g. code ThrottlingException);
            # transport errors (timeouts) do not, so their class names them.
            details = getattr(error, "response", None) or {}
            call.error = details.get("Error", {}).get("Code") or type(error).__name__
            call.retries = details.get("ResponseMetadata", {}).get("RetryAttempts", 0)
            raise
        usage = response.get("usage", {})
        call.input_tokens = usage.get("inputTokens", 0)
        call.output_tokens = usage.get("outputTokens", 0)
        call.retries = response.get("ResponseMetadata", {}).get("RetryAttempts", 0)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


# USD per million tokens (input, output), by model family (a substring of the model ID). These are
# Anthropic's list prices; Bedrock sets its own, and its regional ("us.") profiles may cost more, so
# reported costs are estimates for comparing runs (https://aws.amazon.com/bedrock/pricing/).
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
}


def cost_usd(calls: Iterable[ModelCall]) -> float | None:
    """The estimated cost of these calls; None if a model that used tokens has no price."""
    total = 0.0
    for call in calls:
        if not (call.input_tokens or call.output_tokens):
            continue
        price = next((p for family, p in PRICES_PER_MTOK.items() if family in call.model_id), None)
        if price is None:
            return None
        total += (call.input_tokens * price[0] + call.output_tokens * price[1]) / 1_000_000
    return round(total, 6)
