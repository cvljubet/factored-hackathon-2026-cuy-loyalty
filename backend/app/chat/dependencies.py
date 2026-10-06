from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from pydantic_ai.models import Model

from agents.conversations import DynamoHandoffStore, DynamoSessionStore
from agents.engines.escalation import HandoffStore, InMemoryHandoffStore
from agents.factory import build_orchestrator
from agents.guardrails import BedrockGuardrail, Guardrail, NoOpGuardrail
from agents.local_model import local_model
from agents.model_router import HybridRouter, ModelRouter
from agents.models import (
    BedrockConfig,
    bedrock_inquiry_model,
    bedrock_router_model,
    bedrock_runtime_client,
)
from agents.orchestrator import Orchestrator
from agents.routing import Router, RuleBasedRouter
from agents.serving import dynamodb_serving_table
from agents.sessions import InMemorySessionStore, SessionStore
from app.config import Settings, get_settings
from app.customers.dependencies import get_serving_repository


def bedrock_config(settings: Settings) -> BedrockConfig:
    return BedrockConfig(
        region=settings.bedrock_region,
        inquiry_model_id=settings.bedrock_inquiry_model_id,
        inquiry_fallback_model_id=settings.bedrock_inquiry_fallback_model_id,
        router_model_id=settings.bedrock_router_model_id,
        profile=settings.bedrock_profile,
        read_timeout_seconds=settings.bedrock_read_timeout_seconds,
        max_attempts=settings.bedrock_max_attempts,
    )


def uses_bedrock(settings: Settings) -> bool:
    return settings.agent_llm == "bedrock" or settings.agent_router == "bedrock" or settings.bedrock_guardrail_enabled


# bedrock_client: the shared bedrock-runtime client; each builder creates one from settings if omitted.
def build_inquiry_model(settings: Settings, bedrock_client: Any = None) -> Model:
    if settings.agent_llm == "bedrock":
        return bedrock_inquiry_model(bedrock_config(settings), bedrock_client)
    return local_model()


def build_router(settings: Settings, bedrock_client: Any = None) -> Router:
    if settings.agent_router == "bedrock":
        return HybridRouter(ModelRouter(bedrock_router_model(bedrock_config(settings), bedrock_client)))
    return RuleBasedRouter()


def build_guardrail(settings: Settings, bedrock_client: Any = None) -> Guardrail:
    # Settings refuses only one of the two, so this is "guardrail not configured".
    if settings.bedrock_guardrail_id is None or settings.bedrock_guardrail_version is None:
        return NoOpGuardrail()
    client = bedrock_client or bedrock_runtime_client(bedrock_config(settings))
    return BedrockGuardrail(client, settings.bedrock_guardrail_id, settings.bedrock_guardrail_version)


def build_chat_stores(settings: Settings) -> tuple[SessionStore, HandoffStore]:
    """Sessions and handoffs. DynamoDB: one conversations table for both, through its own AWS session
    (CONVERSATIONS_AWS_PROFILE, or the default chain: the ECS task role), never the Bedrock one.
    Building them makes no request."""
    if settings.conversations_backend == "dynamodb":
        # The serving helper is generic: a table with its own session and short timeouts.
        table = dynamodb_serving_table(
            settings.conversations_table_name, settings.conversations_aws_region, settings.conversations_aws_profile
        )
        sessions = DynamoSessionStore(
            table, idle_minutes=settings.session_idle_minutes, retention_days=settings.session_retention_days
        )
        return sessions, DynamoHandoffStore(table, retention_days=settings.handoff_retention_days)
    return InMemorySessionStore(), InMemoryHandoffStore()


@lru_cache
def get_orchestrator() -> Orchestrator:
    """One orchestrator per process, so in-memory sessions and handoffs (if used) persist across requests."""
    settings = get_settings()
    # One client for every Bedrock call: same credentials, region, timeouts and connection pool.
    client = bedrock_runtime_client(bedrock_config(settings)) if uses_bedrock(settings) else None
    sessions, handoffs = build_chat_stores(settings)
    return build_orchestrator(
        serving=get_serving_repository(),  # the same repository GET /me/profile reads
        model=build_inquiry_model(settings, client),
        router=build_router(settings, client),
        guardrail=build_guardrail(settings, client),
        sessions=sessions,
        handoffs=handoffs,
    )


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
