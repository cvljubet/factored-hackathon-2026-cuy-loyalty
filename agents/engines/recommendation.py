"""Recommendation engine and the contract the propensity model will serve.

The model is not built yet, so the only provider is NotReadyRecommendationProvider
and the engine says recommendations are unavailable. No recommendation is ever
produced without a payload from a provider.
"""

import logging
from datetime import date
from typing import Protocol

from pydantic import BaseModel, Field

from agents.context import AgentContext
from agents.engines.base import EngineResult
from agents.messages import message

logger = logging.getLogger(__name__)


class ProductRecommendation(BaseModel):
    product_type: str
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)
    top_categories: list[str] = Field(default_factory=list)


class RecommendationPayload(BaseModel):
    """{customer_id, model_version, as_of, recommendations: [{product_type, score, rank, top_categories}]}"""

    customer_id: str
    model_version: str
    as_of: date
    recommendations: list[ProductRecommendation]


class RecommendationProvider(Protocol):
    def get_recommendations(self, customer_id: str) -> RecommendationPayload | None:
        """The customer's scored recommendations, or None when there are none to serve."""
        ...


class NotReadyRecommendationProvider:
    """Stands in until the propensity model's output is served; never invents results."""

    def get_recommendations(self, customer_id: str) -> RecommendationPayload | None:
        return None


def payload_for(provider: RecommendationProvider, context: AgentContext) -> RecommendationPayload | None:
    """The provider's payload for the context's customer, refusing one issued for anyone else."""
    payload = provider.get_recommendations(context.customer_id)
    if payload is not None and payload.customer_id != context.customer_id:
        logger.error("Recommendation provider returned a payload for a different customer; discarded")
        raise ValueError("Recommendation payload customer_id does not match the authenticated customer")
    return payload


class RecommendationEngine:
    def __init__(self, provider: RecommendationProvider):
        self.provider = provider

    def handle(self, context: AgentContext, user_message: str) -> EngineResult:
        try:
            payload = payload_for(self.provider, context)
        except Exception:
            logger.exception("Recommendation lookup failed")
            return EngineResult(reply=message("inquiry_failed", context.language), status="failed", failed=True)

        if payload is None or not payload.recommendations:
            return EngineResult(reply=message("recommendations_unavailable", context.language), status="unavailable")

        ranked = sorted(payload.recommendations, key=lambda item: item.rank)
        lines = [message("recommendations_intro", context.language)]
        lines += [f"{item.rank}. {item.product_type}" for item in ranked]
        return EngineResult(reply="\n".join(lines), status="answered")
