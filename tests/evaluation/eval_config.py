"""A snapshot of every lever the evaluated behaviour depends on, stored in each report's header.

evals/compare.py diffs two snapshots, so a change in scores can be read next to what changed:
router and inquiry models and inference settings, the router prompt and output schema, the
inquiry prompt and tool definitions, the keyword rules, the output scan, the published
guardrail's definition, and the datasets themselves (the inquiry suite's fixed data included).

Nothing here calls a model. The guardrail definition comes from GetGuardrail (control plane),
which the invoker role may not call: EVAL_GUARDRAIL_PROFILE names a profile that may
(e.g. the model account's admin); without one the snapshot records why it is missing.
"""

import hashlib
import os
import re
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from eval_kit import DATASETS

# Module attributes holding the keyword rules; a missing name (after a rename) is skipped.
RULES = {
    "agents.routing": ["_HUMAN_REQUEST", "_CREDIT_DECISION", "_RECOMMENDATION", "_INQUIRY", "_PT_WORDS", "_ES_WORDS"],
    "agents.safety": ["_SENSITIVE_PATTERNS", "_DIGIT_RUN", "_ISO_DATE", "_CURRENCY_BEFORE"],
}


def bedrock_config_from_env():
    """The BedrockConfig the live suites use, from the same variables as the backend."""
    from agents.models import HAIKU_4_5, BedrockConfig

    return BedrockConfig(
        region=os.environ.get("BEDROCK_REGION", "us-east-2"),
        router_model_id=os.environ.get("BEDROCK_ROUTER_MODEL_ID", HAIKU_4_5),
        inquiry_model_id=os.environ.get("BEDROCK_INQUIRY_MODEL_ID", HAIKU_4_5),
        # Empty means none, as in the backend's settings (env_ignore_empty).
        inquiry_fallback_model_id=os.environ.get("BEDROCK_INQUIRY_FALLBACK_MODEL_ID") or None,
        profile=os.environ.get("BEDROCK_PROFILE"),
        # Latency does not matter here; riding out throttling does.
        max_attempts=int(os.environ.get("EVAL_MAX_ATTEMPTS", "4")),
    )


def snapshot(live: bool) -> dict[str, Any]:
    config = {
        "router": _router(),
        "inquiry": _inquiry(),
        "rules": _rules(),
        "datasets": _datasets(),
    }
    if live:
        config["inference"] = _inference()
        config["guardrail"] = _guardrail()
    return config


def _router() -> dict[str, Any]:
    from agents.model_router import _instructions, router_agent
    from agents.routing import RouteResult

    # The instructions depend only on the customer's language; "es" stands for both.
    instructions = _instructions(SimpleNamespace(deps=SimpleNamespace(language="es")))
    fields = RouteResult.model_json_schema()["properties"]
    return {
        "instructions": instructions,
        # Field descriptions reach the model as the output tool's schema.
        "output_fields": {name: field.get("description", "") for name, field in fields.items()},
        "output_retries": getattr(router_agent, "_max_output_retries", None),
    }


def _inquiry() -> dict[str, Any]:
    """What the inquiry model is offered, as Pydantic AI sends it: instructions and tool definitions."""
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from agents.context import AgentContext
    from agents.deps import AgentDeps
    from agents.engines.inquiry import MAX_ROUNDS
    from agents.engines.recommendation import NotReadyRecommendationProvider
    from agents.inquiry_agent import inquiry_agent
    from agents.serving import InMemoryServingRepository

    offered: list[AgentInfo] = []

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.append(info)
        return ModelResponse(parts=[TextPart("ok")])

    # The instructions depend only on the customer's language; "es" stands for both.
    context = AgentContext(customer_id="SNAPSHOT", session_id="snapshot", language="es")
    deps = AgentDeps(context, InMemoryServingRepository(), NotReadyRecommendationProvider())
    inquiry_agent.run_sync("snapshot", deps=deps, model=FunctionModel(capture))
    info = offered[0]
    return {
        "instructions": info.instructions,
        "tools": {
            tool.name: {"description": tool.description, "parameters": tool.parameters_json_schema}
            for tool in info.function_tools
        },
        "max_rounds": MAX_ROUNDS,
        "tool_retries": getattr(inquiry_agent, "_max_tool_retries", None),
    }


def _rules() -> dict[str, Any]:
    import importlib

    out: dict[str, Any] = {}
    for module_name, names in RULES.items():
        module = importlib.import_module(module_name)
        for name in names:
            value = getattr(module, name, None)
            if value is None:
                continue
            key = f"{module_name.rsplit('.', 1)[-1]}.{name.lstrip('_')}"
            out[key] = _pattern_text(value)
    return out


def _pattern_text(value: Any) -> Any:
    if isinstance(value, re.Pattern):
        return value.pattern
    if isinstance(value, (frozenset, set)):
        return sorted(value)
    if isinstance(value, (tuple, list)):
        return [_pattern_text(item) for item in value]
    return value


def _datasets() -> dict[str, Any]:
    """Case files (.jsonl) and fixed data (.json, e.g. what the inquiry tools serve)."""
    out = {}
    for path in sorted(Path(DATASETS).glob("*.json*")):
        data = path.read_bytes()
        entry: dict[str, Any] = {"sha256": hashlib.sha256(data).hexdigest()[:12]}
        if path.suffix == ".jsonl":
            entry["cases"] = sum(1 for line in data.splitlines() if line.strip())
        out[path.name] = entry
    return out


def _inference() -> dict[str, Any]:
    import boto3

    from agents.models import bedrock_model
    from eval_kit import PRICES_PER_MTOK

    config = bedrock_config_from_env()
    # A client with dummy credentials: building the model makes no request, it only exposes its settings.
    client = boto3.Session(aws_access_key_id="x", aws_secret_access_key="x", region_name=config.region).client(
        "bedrock-runtime"
    )
    # The router and inquiry models get the same settings (agents.models.bedrock_model).
    settings = bedrock_model(config.router_model_id, config, client).settings or {}
    values = {key: value for key, value in asdict(config).items() if key != "profile"}
    values.update(
        model_settings=dict(settings),
        eval_model_rpm=float(os.environ.get("EVAL_MODEL_RPM", "9")),
        # What turns tokens into the reports' cost estimates (eval_kit.cost_usd).
        prices_per_mtok={family: list(prices) for family, prices in PRICES_PER_MTOK.items()},
    )
    return values


def _guardrail() -> dict[str, Any]:
    guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID")
    version = os.environ.get("BEDROCK_GUARDRAIL_VERSION")
    if not guardrail_id or not version:
        return {"error": "BEDROCK_GUARDRAIL_ID / BEDROCK_GUARDRAIL_VERSION not set"}
    summary: dict[str, Any] = {"id": guardrail_id, "version": version}
    try:
        import boto3

        profile = os.environ.get("EVAL_GUARDRAIL_PROFILE") or os.environ.get("BEDROCK_PROFILE")
        session = boto3.Session(profile_name=profile, region_name=os.environ.get("BEDROCK_REGION", "us-east-2"))
        response = session.client("bedrock").get_guardrail(guardrailIdentifier=guardrail_id, guardrailVersion=version)
    except Exception as error:  # AccessDenied for the invoker role, or no credentials
        summary["error"] = f"{type(error).__name__}: {error}"[:300]
        return summary
    summary.update(_guardrail_definition(response))
    return summary


def _guardrail_definition(response: dict[str, Any]) -> dict[str, Any]:
    """GetGuardrail reduced to what decides a verdict, keyed by name so diffs read per topic/filter."""
    topics = {
        topic["name"]: {
            "definition": topic.get("definition"),
            "examples": topic.get("examples", []),
            "input": f"{topic.get('inputAction')}/{'on' if topic.get('inputEnabled', True) else 'off'}",
            "output": f"{topic.get('outputAction')}/{'on' if topic.get('outputEnabled', True) else 'off'}",
        }
        for topic in response.get("topicPolicy", {}).get("topics", [])
    }
    content = {
        f["type"]: f"input {f.get('inputStrength')} / output {f.get('outputStrength')}"
        for f in response.get("contentPolicy", {}).get("filters", [])
    }
    sensitive = response.get("sensitiveInformationPolicy", {})
    pii = {
        e["type"]: f"input {e.get('inputAction', e.get('action'))} / output {e.get('outputAction', e.get('action'))}"
        for e in sensitive.get("piiEntities", [])
    }
    regexes = {r["name"]: f"{r.get('pattern')} -> {r.get('action')}" for r in sensitive.get("regexes", [])}
    words = response.get("wordPolicy", {})
    grounding = {
        f["type"]: f.get("threshold") for f in response.get("contextualGroundingPolicy", {}).get("filters", [])
    }
    return {
        "name": response.get("name"),
        "updated_at": str(response.get("updatedAt")),
        "topic_tier": response.get("topicPolicy", {}).get("tier", {}).get("tierName"),
        "content_tier": response.get("contentPolicy", {}).get("tier", {}).get("tierName"),
        "topics": topics,
        "content_filters": content,
        "pii": pii,
        "regexes": regexes,
        "words": sorted(w.get("text", "") for w in words.get("words", [])),
        "managed_word_lists": sorted(w.get("type", "") for w in words.get("managedWordLists", [])),
        "contextual_grounding": grounding,
    }
