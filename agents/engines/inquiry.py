import logging

from pydantic_ai import UsageLimits, capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel

from agents.context import AgentContext
from agents.deps import AgentDeps
from agents.engines.base import EngineResult
from agents.engines.recommendation import RecommendationProvider
from agents.inquiry_agent import inquiry_agent
from agents.messages import message
from agents.serving import ServingRepository
from agents.sessions import VerifiedFact
from agents.tools import ToolResult

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


class InquiryEngine:
    """Answers questions about the customer's own data by running the Pydantic AI inquiry agent.

    At most max_rounds model requests per turn: UsageLimits enforces the cap, and
    tools requested in the last round are not run (see inquiry_agent). Running out
    of rounds, a model error or an empty answer fails the turn.
    """

    def __init__(
        self,
        model: Model,
        serving: ServingRepository,
        recommendations: RecommendationProvider,
        max_rounds: int = MAX_ROUNDS,
    ):
        self.model = model
        self.serving = serving
        self.recommendations = recommendations
        self.max_rounds = max_rounds

    def handle(self, context: AgentContext, user_message: str) -> EngineResult:
        deps = AgentDeps(context, self.serving, self.recommendations, self.max_rounds)
        with capture_run_messages() as messages:
            try:
                result = inquiry_agent.run_sync(
                    user_message,
                    deps=deps,
                    model=self.model,
                    usage_limits=UsageLimits(request_limit=self.max_rounds),
                )
                reply = result.output.strip()
            # RoundLimitReached, UsageLimitExceeded, model/API errors and exhausted retries.
            except Exception as error:
                logger.warning("Inquiry turn failed: %s", type(error).__name__, exc_info=True)
                reply = ""

        tools_called, model_requests = _trace(messages)
        trace = {
            "tools_called": tools_called,
            "model_requests": model_requests,
            "facts": _verified_facts(messages),
            **_usage(messages, _fallback_model_names(self.model)),
        }
        if not reply:
            return EngineResult(reply=message("inquiry_failed", context.language), status="failed", failed=True, **trace)
        return EngineResult(reply=reply, status="answered", **trace)


def _trace(messages: list[ModelMessage]) -> tuple[tuple[str, ...], int]:
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    calls = tuple(part.tool_name for m in responses for part in m.parts if isinstance(part, ToolCallPart))
    return calls, len(responses)


def _usage(messages: list[ModelMessage], fallback_names: set[str]) -> dict:
    """Tokens and the models that answered this turn, from the responses the run received."""
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    models_used = tuple(dict.fromkeys(m.model_name for m in responses if m.model_name))
    return {
        "input_tokens": sum(m.usage.input_tokens for m in responses),
        "output_tokens": sum(m.usage.output_tokens for m in responses),
        "models_used": models_used,
        "fallback_used": any(name in fallback_names for name in models_used),
    }


def _fallback_model_names(model: Model) -> set[str]:
    """Every model after the first in a FallbackModel; none for a single model."""
    if isinstance(model, FallbackModel):
        return {fallback.model_name for fallback in model.models[1:]}
    return set()


def _verified_facts(messages: list[ModelMessage]) -> tuple[VerifiedFact, ...]:
    """Tool results with status "ok": data the assistant actually retrieved this turn."""
    return tuple(
        VerifiedFact(tool=part.content.tool, data=part.content.data or {})
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, ToolResult) and part.content.status == "ok"
    )
