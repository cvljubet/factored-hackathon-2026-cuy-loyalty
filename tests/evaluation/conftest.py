"""Evaluation suites for routing and guardrails: labelled datasets scored case by case.

Offline suites (rule router, deterministic output scan) run with every pytest run.
Live suites call Bedrock and run only with EVAL_LIVE=1:

    EVAL_LIVE=1 BEDROCK_PROFILE=<model-account-profile> \
    BEDROCK_GUARDRAIL_ID=<id> BEDROCK_GUARDRAIL_VERSION=<n> \
    uv run pytest tests/evaluation -v

A scorecard per suite is printed at the end of the run. EVAL_REPORT=<name> also writes
every case's result as JSON lines to evals/reports/<name>.jsonl (a path with a directory is
used as given), headed by a line describing the run and snapshotting its levers (see
eval_config.py); compare two runs with evals/compare.py.

Haiku calls are paced to EVAL_MODEL_RPM requests per minute (default 9, under the model
account's quota of 10), and retried up to EVAL_MAX_ATTEMPTS times in all (default 4).
"""

import json
import os
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from eval_config import bedrock_config_from_env, snapshot
from eval_kit import Scorecard

LIVE = os.environ.get("EVAL_LIVE") == "1"
REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "evals" / "reports"


_scorecard = Scorecard()


@pytest.fixture(scope="session")
def scorecard() -> Scorecard:
    return _scorecard


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: calls Bedrock; runs only with EVAL_LIVE=1")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live evaluation; set EVAL_LIVE=1 and Bedrock settings")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if not _scorecard.results:
        return
    terminalreporter.write_sep("=", "evaluation scorecard")
    for line in _scorecard.lines():
        terminalreporter.write_line(line)
    name = os.environ.get("EVAL_REPORT")
    if name:
        report = report_path(name)
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("w", encoding="utf-8") as handle:
            header = {"run": run_metadata(), "config": config_snapshot()}
            handle.write(json.dumps(header, ensure_ascii=False, default=str) + "\n")
            for result in _scorecard.results:
                handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
        terminalreporter.write_line(f"Per-case results written to {report}")


def config_snapshot() -> dict[str, Any]:
    # A snapshot that cannot be taken must not cost the run its results.
    try:
        return snapshot(LIVE)
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"[:300]}


def report_path(name: str) -> Path:
    path = Path(name)
    if path.parent != Path("."):
        return path
    return REPORTS_DIR / (name if name.endswith(".jsonl") else f"{name}.jsonl")


def run_metadata() -> dict[str, Any]:
    """What was evaluated, so two reports can be told apart."""

    def git(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return "unknown"

    return {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--", "agents", "tests/evaluation")),
        "live": LIVE,
        "bedrock_region": os.environ.get("BEDROCK_REGION"),
        "router_model_id": os.environ.get("BEDROCK_ROUTER_MODEL_ID"),
        "guardrail_id": os.environ.get("BEDROCK_GUARDRAIL_ID"),
        "guardrail_version": os.environ.get("BEDROCK_GUARDRAIL_VERSION"),
    }


# Live fixtures. They build the same objects the backend builds, from the same BEDROCK_*
# variables, without the rest of the backend Settings (Cognito etc.).


@pytest.fixture(scope="session")
def bedrock_config():
    return bedrock_config_from_env()


@pytest.fixture(scope="session")
def model_pacer():
    from eval_kit import Pacer

    return Pacer(float(os.environ.get("EVAL_MODEL_RPM", "9")))


@pytest.fixture(scope="session")
def bedrock_client(bedrock_config):
    from agents.models import bedrock_runtime_client

    # tests/unit/conftest.py swaps in dummy credentials for the whole process when it is collected.
    if os.environ.get("AWS_ACCESS_KEY_ID") == "testing":
        pytest.fail("Run the live evaluation on its own: uv run pytest tests/evaluation")
    return bedrock_runtime_client(bedrock_config)


@pytest.fixture(scope="session")
def guardrail_ids() -> tuple[str, str]:
    guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID")
    version = os.environ.get("BEDROCK_GUARDRAIL_VERSION")
    if not guardrail_id or not version:
        pytest.fail("Set BEDROCK_GUARDRAIL_ID and BEDROCK_GUARDRAIL_VERSION for the guardrail evaluation")
    return guardrail_id, version
