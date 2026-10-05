from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from pydantic_ai.models import Model

from agents.factory import build_orchestrator
from agents.local_model import local_model
from agents.model_router import HybridRouter, ModelRouter
from agents.models import BedrockConfig, bedrock_inquiry_model, bedrock_router_model
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


def _bedrock_config(settings: Settings) -> BedrockConfig:
    return BedrockConfig(
        region=settings.bedrock_region,
        inquiry_model_id=settings.bedrock_inquiry_model_id,
        inquiry_fallback_model_id=settings.bedrock_inquiry_fallback_model_id,
        router_model_id=settings.bedrock_router_model_id,
    )


def build_inquiry_model(settings: Settings) -> Model:
    if settings.agent_llm == "bedrock":
        return bedrock_inquiry_model(_bedrock_config(settings))
    return local_model()


def build_router(settings: Settings) -> Router:
    if settings.agent_router == "bedrock":
        return HybridRouter(ModelRouter(bedrock_router_model(_bedrock_config(settings))))
    return RuleBasedRouter()


@lru_cache
def get_orchestrator() -> Orchestrator:
    """One orchestrator per process, so in-memory sessions and handoffs persist across requests."""
    settings = get_settings()
    return build_orchestrator(
        profiles=RepositoryProfileSource(get_customer_repository()),
        model=build_inquiry_model(settings),
        router=build_router(settings),
    )


OrchestratorDep = Annotated[Orchestrator, Depends(get_orchestrator)]
