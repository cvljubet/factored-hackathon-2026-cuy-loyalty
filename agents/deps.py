from dataclasses import dataclass

from agents.context import AgentContext
from agents.engines.recommendation import RecommendationProvider
from agents.serving import ServingRepository


@dataclass(frozen=True)
class AgentDeps:
    """Pydantic AI dependencies for one inquiry run (tools receive them as RunContext[AgentDeps]).

    context.customer_id is the only customer identity any tool can use. It is set by
    the backend from the verified token, and nothing the model sends can change it.
    """

    context: AgentContext
    # Customer and reference data (DynamoDB customer-serving table, or the in-memory stand-in).
    serving: ServingRepository
    recommendations: RecommendationProvider
    # Model rounds allowed per turn; tools requested in the last round are not run.
    max_rounds: int = 3

    @property
    def customer_id(self) -> str:
        return self.context.customer_id
