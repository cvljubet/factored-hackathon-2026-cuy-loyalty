from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agents.context import AgentContext
from agents.engines.recommendation import RecommendationProvider


class ProfileSource(Protocol):
    """Reads the minimal profile of a customer (the backend adapts CustomerRepository to this)."""

    def get_profile(self, customer_id: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class AgentDeps:
    """Pydantic AI dependencies for one inquiry run (tools receive them as RunContext[AgentDeps]).

    context.customer_id is the only customer identity any tool can use. It is set by
    the backend from the verified token, and nothing the model sends can change it.
    New data sources (products, spending, ...) become fields here as they are built.
    """

    context: AgentContext
    profiles: ProfileSource
    recommendations: RecommendationProvider
    # Model rounds allowed per turn; tools requested in the last round are not run.
    max_rounds: int = 3

    @property
    def customer_id(self) -> str:
        return self.context.customer_id
