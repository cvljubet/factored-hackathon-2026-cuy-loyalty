"""Model-based routing with Pydantic AI structured output (for the future Bedrock Haiku router).

Not enabled by default: the backend uses RuleBasedRouter until Bedrock is
available. HybridRouter keeps the safety-relevant decisions deterministic by
OR-ing the rule checks into whatever the model returns.
"""

import logging

from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.models import Model

from agents.context import AgentContext
from agents.routing import RouteResult, Router, RuleBasedRouter, is_credit_decision, is_human_request
from agents.safety import is_sensitive_request

logger = logging.getLogger(__name__)

# output_type=RouteResult: Pydantic AI asks for a structured tool call and validates it
# into the model, retrying on invalid output; no JSON is parsed by hand.
router_agent = Agent(
    deps_type=AgentContext,
    output_type=RouteResult,
    name="loyalty_router",
    retries=1,
)


@router_agent.instructions
def _instructions(ctx: RunContext[AgentContext]) -> str:
    return f"""Classify one message a bank customer sent to the loyalty assistant.

engine:
- inquiry: questions about their own profile, products, balances, spending, campaigns, benefits,
  transactions, contacts, complaints, branches or exchange rates
- recommendation: asks which product they should get or for suggestions
- escalation: asks for a human, or asks whether they would be approved or are eligible for credit
- out_of_scope: anything else
language: the language of the message, es or pt. If unclear, use {ctx.deps.language}.
confidence: from 0 to 1.
sensitive_request: true if they ask about their own credit score or income.
credit_decision: true if they ask for an individual credit or eligibility decision.
"""


class ModelRouter:
    """Routes with an LLM; returns the validated RouteResult (structured output)."""

    def __init__(self, model: Model):
        self.model = model

    def route(self, context: AgentContext, message: str) -> RouteResult:
        result = router_agent.run_sync(
            message, deps=context, model=self.model, usage_limits=UsageLimits(request_limit=2)
        )
        return result.output


class HybridRouter:
    """The model router's answer with deterministic safety checks OR-ed in.

    Falls back to the rule-based router if the model call fails, so routing never
    blocks a turn.
    """

    def __init__(self, model_router: Router, fallback: Router | None = None):
        self.model_router = model_router
        self.fallback = fallback or RuleBasedRouter()

    def route(self, context: AgentContext, message: str) -> RouteResult:
        try:
            routed = self.model_router.route(context, message)
        except Exception:
            logger.warning("Model router failed; using the rule-based router", exc_info=True)
            return self.fallback.route(context, message)

        credit = routed.credit_decision or is_credit_decision(message)
        human = is_human_request(message)
        sensitive = routed.sensitive_request or is_sensitive_request(message)
        engine = "escalation" if credit or human else routed.engine
        return routed.model_copy(update={"engine": engine, "credit_decision": credit, "sensitive_request": sensitive})
