import logging

from pydantic_ai import UsageLimits, capture_run_messages
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model

from agents.context import AgentContext
from agents.deps import AgentDeps, ProfileSource
from agents.engines.base import EngineResult
from agents.engines.recommendation import RecommendationProvider
from agents.inquiry_agent import inquiry_agent
from agents.messages import message
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
        profiles: ProfileSource,
        recommendations: RecommendationProvider,
        max_rounds: int = MAX_ROUNDS,
    ):
        self.model = model
        self.profiles = profiles
        self.recommendations = recommendations
        self.max_rounds = max_rounds

    def handle(self, context: AgentContext, user_message: str) -> EngineResult:
        deps = AgentDeps(context, self.profiles, self.recommendations, self.max_rounds)
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
        trace = {"tools_called": tools_called, "model_requests": model_requests, "facts": _verified_facts(messages)}
        if not reply:
            return EngineResult(reply=message("inquiry_failed", context.language), status="failed", failed=True, **trace)
        return EngineResult(reply=reply, status="answered", **trace)


def _trace(messages: list[ModelMessage]) -> tuple[tuple[str, ...], int]:
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    calls = tuple(part.tool_name for m in responses for part in m.parts if isinstance(part, ToolCallPart))
    return calls, len(responses)


def _verified_facts(messages: list[ModelMessage]) -> tuple[VerifiedFact, ...]:
    """Tool results with status "ok": data the assistant actually retrieved this turn."""
    return tuple(
        VerifiedFact(tool=part.content.tool, data=part.content.data or {})
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, ToolReturnPart) and isinstance(part.content, ToolResult) and part.content.status == "ok"
    )
