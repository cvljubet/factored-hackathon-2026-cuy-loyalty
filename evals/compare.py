"""Compare two evaluation reports (tests/evaluation, EVAL_REPORT=<name>) case by case.

    uv run python evals/compare.py evals/reports/baseline.jsonl evals/reports/guardrail-v2.jsonl
    uv run python evals/compare.py baseline guardrail-v2          # names under evals/reports/
    uv run python evals/compare.py baseline guardrail-v2 --fail-on-regression

Prints what each run evaluated, what changed in its levers (router model and settings,
router prompt and output schema, keyword rules, output scan, guardrail definition, datasets;
see tests/evaluation/eval_config.py), the pass rate per suite and per tag in both runs, then
every case whose outcome changed. Cases where the model call failed ("model_error") are
counted apart: they say nothing about the model, so a rise in errors is not a regression.

    uv run python evals/compare.py baseline guardrail-v2 --config-only   # just the lever diff
"""

import argparse
import difflib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPORTS_DIR = Path(__file__).resolve().parent / "reports"
ERROR = "model_error"
# Tags whose pass rate is shown per suite; safety must stay at 100%.
WATCHED_TAGS = ("safety", "false-block-trap", "false-sensitive-trap", "paraphrase")

Key = tuple[str, str]  # (suite, case_id)


def resolve(name: str) -> Path:
    path = Path(name)
    if not path.exists() and path.parent == Path("."):
        path = REPORTS_DIR / (name if name.endswith(".jsonl") else f"{name}.jsonl")
    try:
        return path.resolve().relative_to(Path.cwd())
    except ValueError:
        return path


def load(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[Key, dict[str, Any]]]:
    """The run header and config snapshot (empty in older reports) and the results by case."""
    run: dict[str, Any] = {}
    config: dict[str, Any] = {}
    results: dict[Key, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if "run" in row:
            run, config = row["run"], row.get("config", {})
        else:
            results[(row["suite"], row["case_id"])] = row
    return run, config, results


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Nested dicts as {"guardrail.topics.<name>.definition": ...}; lists and scalars are leaves."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def short(value: Any, limit: int = 100) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= limit else text[: limit - 3] + "..."


def config_changes(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Each lever that differs, as readable lines: old -> new, list items added and removed,
    or a line diff for long texts such as the router prompt."""
    if not a or not b:
        missing = " and ".join(side for side, config in (("A", a), ("B", b)) if not config)
        return [f"no config snapshot in {missing} (report written before snapshots existed)"]
    lines: list[str] = []
    for section in sorted(a.keys() ^ b.keys()):
        side = "A" if section in a else "B"
        lines.append(f"{section}: only in {side} (the other run was offline or could not read it)")
    flat_a = flatten({k: v for k, v in a.items() if k in b})
    flat_b = flatten({k: v for k, v in b.items() if k in a})
    for path in sorted(flat_a.keys() | flat_b.keys()):
        old, new = flat_a.get(path), flat_b.get(path)
        if old == new:
            continue
        if path not in flat_a:
            lines.append(f"{path}: added {short(new)}")
        elif path not in flat_b:
            lines.append(f"{path}: removed (was {short(old)})")
        elif isinstance(old, list) and isinstance(new, list):
            old_items, new_items = [json.dumps(i, ensure_ascii=False) for i in old], [
                json.dumps(i, ensure_ascii=False) for i in new
            ]
            lines.append(f"{path}:")
            lines += [f"    + {item}" for item in new_items if item not in old_items]
            lines += [f"    - {item}" for item in old_items if item not in new_items]
            if set(old_items) == set(new_items):
                lines.append("    (same items, different order)")
        elif isinstance(old, str) and isinstance(new, str) and ("\n" in old + new or len(old + new) > 160):
            lines.append(f"{path}:")
            diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            # "+ " / "- " then the line, so a prompt line that itself starts with "- " stays readable.
            lines += [f"    {line[0]} {line[1:]}" for line in diff if not line.startswith(("---", "+++", "@@"))]
        else:
            lines.append(f"{path}: {short(old)} -> {short(new)}")
    return lines


def describe(label: str, path: Path, run: dict[str, Any]) -> str:
    if not run:
        return f"{label}: {path} (no run header)"
    dirty = "+uncommitted" if run.get("git_dirty") else ""
    guardrail = f"{run.get('guardrail_id')} v{run.get('guardrail_version')}" if run.get("guardrail_id") else "none"
    return (
        f"{label}: {path}\n"
        f"   {run.get('created_at')}  commit {run.get('git_commit')}{dirty}  live={run.get('live')}\n"
        f"   guardrail {guardrail}  router {run.get('router_model_id') or 'default'}"
    )


def score(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "-"
    errors = sum(row["actual"] == ERROR for row in rows)
    scored = len(rows) - errors
    passed = sum(row["passed"] for row in rows)
    rate = f"{passed / scored:.0%}" if scored else "n/a"
    return f"{passed}/{scored} {rate}" + (f" ({errors} err)" if errors else "")


def summary(a: dict[Key, dict[str, Any]], b: dict[Key, dict[str, Any]]) -> list[str]:
    groups: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for side, results in ((0, a), (1, b)):
        for (suite, _), row in results.items():
            groups[suite][side].append(row)
            for tag in row.get("tags", []):
                if tag in WATCHED_TAGS:
                    groups[f"{suite}  tag={tag}"][side].append(row)
    width = max((len(name) for name in groups), default=10)
    lines = [f"{'suite':<{width}}  {'A':>18}  {'B':>18}"]
    for name in sorted(groups):
        rows_a, rows_b = groups[name]
        lines.append(f"{name:<{width}}  {score(rows_a):>18}  {score(rows_b):>18}")
    return lines


def changes(a: dict[Key, dict[str, Any]], b: dict[Key, dict[str, Any]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    suites_a, suites_b = {suite for suite, _ in a}, {suite for suite, _ in b}
    # A suite that only one run has (e.g. an offline run has no live suites) is one line, not one per case.
    for suite in sorted(suites_a ^ suites_b):
        side = "A" if suite in suites_a else "B"
        count = sum(key[0] == suite for key in (a if side == "A" else b))
        out[f"only in {side}"].append(f"{suite}  (whole suite, {count} cases)")
    for key in sorted(a.keys() | b.keys()):
        suite, case_id = key
        if suite not in suites_a or suite not in suites_b:
            continue
        old, new = a.get(key), b.get(key)
        if old is None or new is None:
            out["only in A" if new is None else "only in B"].append(f"{suite}  {case_id}")
            continue
        line = f"{suite}  {case_id}: expected {new['expected']}, {old['actual']} -> {new['actual']}"
        if ERROR in (old["actual"], new["actual"]):
            if old["actual"] != new["actual"]:
                out["model errors appeared or cleared"].append(line)
        elif old["passed"] and not new["passed"]:
            out["REGRESSIONS (pass -> fail)"].append(line)
        elif not old["passed"] and new["passed"]:
            out["fixes (fail -> pass)"].append(line)
        elif old["actual"] != new["actual"]:
            out["still failing, different answer"].append(line)
        elif old.get("detail", {}).get("policies") != new.get("detail", {}).get("policies"):
            # Same verdict, but a different guardrail policy intervened.
            policies = f"{old['detail'].get('policies')} -> {new['detail'].get('policies')}"
            out["same verdict, different guardrail policy"].append(f"{suite}  {case_id}: {policies}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("a", help="baseline report (path, or name under evals/reports/)")
    parser.add_argument("b", help="report to compare against the baseline")
    parser.add_argument("--fail-on-regression", action="store_true", help="exit 1 if any case went pass -> fail")
    parser.add_argument("--config-only", action="store_true", help="show only what changed in the levers")
    args = parser.parse_args()

    path_a, path_b = resolve(args.a), resolve(args.b)
    run_a, config_a, a = load(path_a)
    run_b, config_b, b = load(path_b)
    print(describe("A", path_a, run_a))
    print(describe("B", path_b, run_b))
    print("\nWhat changed (A -> B):")
    lever_changes = config_changes(config_a, config_b)
    print("\n".join(f"  {line}" for line in lever_changes) if lever_changes else "  nothing")
    if any(line.startswith("datasets.") for line in lever_changes):
        print("  NOTE: the datasets differ, so score changes also reflect added, removed or relabelled cases.")
    if args.config_only:
        return 0
    print()
    print("\n".join(summary(a, b)))
    found = changes(a, b)
    for title in sorted(found, key=lambda t: (not t.startswith("REGRESSIONS"), t)):
        print(f"\n{title}:")
        for line in found[title]:
            print(f"  {line}")
    if not found:
        print("\nNo case changed outcome.")
    return 1 if args.fail_on_regression and found.get("REGRESSIONS (pass -> fail)") else 0


if __name__ == "__main__":
    sys.exit(main())
