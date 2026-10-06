"""Shared helpers for the evaluation suites: datasets, parametrisation and the scorecard."""

import json
import time
from collections import defaultdict
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

    def record(self, suite: str, case: dict[str, Any], expected: str, actual: str, **detail: Any) -> bool:
        passed = expected == actual
        self.results.append(
            CaseResult(suite, case["id"], passed, expected, actual, case.get("tags", []), detail)
        )
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
    """Per expected label (e.g. engine, or block/allow) and per tag."""
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
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
