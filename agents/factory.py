"""Assembles the orchestrator. Every collaborator is injectable; defaults are the local stand-ins."""

from pydantic_ai.models import Model

from agents.engines.escalation import EscalationEngine, HandoffStore, InMemoryHandoffStore
from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import NotReadyRecommendationProvider, RecommendationEngine, RecommendationProvider
from agents.guardrails import Guardrail, NoOpGuardrail
from agents.local_model import local_model
from agents.orchestrator import Orchestrator
from agents.routing import Router, RuleBasedRouter
from agents.serving import ServingRepository
from agents.sessions import InMemorySessionStore, SessionStore


def build_orchestrator(
    *,
    serving: ServingRepository,
    model: Model | None = None,
    router: Router | None = None,
    recommendations: RecommendationProvider | None = None,
    handoffs: HandoffStore | None = None,
    sessions: SessionStore | None = None,
    guardrail: Guardrail | None = None,
) -> Orchestrator:
    """model is the inquiry agent's model: the local stand-in by default, Bedrock when configured."""
    recommendations = recommendations or NotReadyRecommendationProvider()
    return Orchestrator(
        router=router or RuleBasedRouter(),
        inquiry=InquiryEngine(model or local_model(), serving, recommendations),
        recommendation=RecommendationEngine(recommendations),
        escalation=EscalationEngine(handoffs if handoffs is not None else InMemoryHandoffStore()),
        guardrail=guardrail or NoOpGuardrail(),
        sessions=sessions if sessions is not None else InMemorySessionStore(),
    )
