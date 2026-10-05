from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from pydantic_ai.models import Model

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
from app.config import Settings, get_settings
from app.customers.dependencies import get_customer_repository
from app.customers.repository import CustomerRepository
from app.customers.service import CustomerNotFoundError, get_customer_profile


class RepositoryProfileSource:
    """Serves the agent's get_my_profile tool from the CustomerRepository (minimal profile only)."""

    def __init__(self, repository: CustomerRepository):
        self.repository = repository

    def get_profile(self, customer_id: str) -> Mapping[str, Any] | None:
        try:
            return get_customer_profile(self.repository, customer_id).model_dump()
        except CustomerNotFoundError:
            return None


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


@lru_cache
def get_orchestrator() -> Orchestrator:
    """One orchestrator per process, so in-memory sessions and handoffs persist across requests."""
    settings = get_settings()
    # One client for every Bedrock call: same credentials, region, timeouts and connection pool.
    client = bedrock_runtime_client(bedrock_config(settings)) if uses_bedrock(settings) else None
    return build_orchestrator(
        profiles=RepositoryProfileSource(get_customer_repository()),
        model=build_inquiry_model(settings, client),
        router=build_router(settings, client),
        guardrail=build_guardrail(settings, client),
    )


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
