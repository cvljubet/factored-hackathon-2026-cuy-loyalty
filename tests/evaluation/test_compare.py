"""evals/compare.py's cost and latency section, on hand-made report rows."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "evals" / "compare.py"
_spec = importlib.util.spec_from_file_location("compare_script", SCRIPT)
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


def turn(case_id: str, actual: str = "pass", cost: float | None = 0.002, requests: int = 2) -> tuple:
    metrics = {
        "requests": requests,
        "input_tokens": 3000,
        "output_tokens": 80,
        "latency_ms": 1000.0,
        "fallback_used": False,
        "retries": 0,
        "cost_usd": cost,
    }
    row = {"actual": actual, "passed": actual == "pass", "detail": {"metrics": metrics}}
    return ("inquiry/turn", case_id), row


def test_compares_only_the_cases_both_runs_measured():
    a = dict([turn("c1"), turn("c2", requests=3), turn("c3", actual="model_error")])
    b = dict([turn("c1", requests=3), turn("c2", requests=3), turn("c3"), turn("c4")])

    lines = compare.cost_and_latency(a, b)

    assert lines[0] == "inquiry/turn (2 cases measured in both runs)"
    assert "  requests / case              2.50 -> 3.00        (+20%)" in lines


def test_an_unpriced_model_makes_the_cost_unknown():
    lines = compare.cost_and_latency(dict([turn("c1")]), dict([turn("c1", cost=None)]))

    assert "  est. cost USD (total)      0.0020 -> n/a" in lines
