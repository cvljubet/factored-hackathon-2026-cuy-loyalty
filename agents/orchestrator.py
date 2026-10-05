"""One chat turn: guardrails, routing, engine dispatch, output safety and escalation.

This is deliberately plain application code, not part of any LLM agent: the
safety policy, failure counting and escalation must behave the same whichever
model is plugged in.
"""

import logging

from pydantic import BaseModel, ConfigDict, Field

from agents.context import DEFAULT_LANGUAGE, AgentContext, Language
from agents.engines.base import EngineResult, ResultStatus
from agents.engines.escalation import EscalationEngine
from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import RecommendationEngine
from agents.guardrails import Guardrail
from agents.messages import message
from agents.routing import Engine, RouteResult, Router
from agents.safety import redact, scan_output
from agents.sessions import MAX_TURNS, ConversationTurn, SessionState, SessionStore, VerifiedFact, merge_facts

logger = logging.getLogger(__name__)

FAILURES_BEFORE_ESCALATION = 2


class TurnTrace(BaseModel):
    """How a turn was handled, for logs and evaluation (not sent to the client)."""

    model_config = ConfigDict(frozen=True)

    route: RouteResult | None = None
    tools_called: tuple[str, ...] = ()
    model_requests: int = 0
    blocked_reason: str | None = None
    consecutive_failures: int = Field(default=0, ge=0)


class AgentReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: str
    reply: str
    engine: Engine
    language: Language
    status: ResultStatus
    handoff_id: str | None = None
    trace: TurnTrace = TurnTrace()

    @property
    def escalated(self) -> bool:
        return self.handoff_id is not None


class Orchestrator:
    def __init__(
        self,
        *,
        router: Router,
        inquiry: InquiryEngine,
        recommendation: RecommendationEngine,
        escalation: EscalationEngine,
        guardrail: Guardrail,
        sessions: SessionStore,
    ):
        self.router = router
        self.inquiry = inquiry
        self.recommendation = recommendation
        self.escalation = escalation
        self.guardrail = guardrail
        self.sessions = sessions

    def handle(
        self, *, customer_id: str, session_id: str, user_message: str, language_hint: Language | None = None
    ) -> AgentReply:
        """Run one turn for the authenticated customer.

        customer_id must be the verified identity's customer_id; it is the only one
        any engine or tool will see.
        """
        state = self.sessions.load(customer_id, session_id)
        context = AgentContext(
            customer_id=customer_id,
            session_id=session_id,
            language=language_hint or state.language or DEFAULT_LANGUAGE,
            consecutive_failures=state.consecutive_failures,
        )

        # The conversation so far, including this message, as a human agent would see it.
        history = (*state.turns, ConversationTurn(role="customer", text=redact(user_message)))[-MAX_TURNS:]

        context, route, engine, result, blocked_reason = self._run(context, user_message, history, state.facts)
        trace_from = result
        facts = merge_facts(state.facts, result.facts)

        failures = context.consecutive_failures + 1 if result.failed else 0
        if result.failed and failures >= FAILURES_BEFORE_ESCALATION:
            engine = "escalation"
            result = self.escalation.escalate(context, "repeated_failures", user_message, history, facts)
        if result.escalated:
            failures = 0

        turns = (*history, ConversationTurn(role="assistant", text=redact(result.reply)))[-MAX_TURNS:]
        self.sessions.save(
            customer_id,
            session_id,
            SessionState(consecutive_failures=failures, language=context.language, turns=turns, facts=facts),
        )
        trace = TurnTrace(
            route=route,
            tools_called=trace_from.tools_called,
            model_requests=trace_from.model_requests,
            blocked_reason=blocked_reason,
            consecutive_failures=failures,
        )
        logger.info(
            "Chat turn: engine=%s status=%s tools=%s failures=%d", engine, result.status, trace.tools_called, failures
        )
        return AgentReply(
            session_id=session_id,
            reply=result.reply,
            engine=engine,
            language=context.language,
            status=result.status,
            handoff_id=result.handoff_id,
            trace=trace,
        )

    def _run(
        self,
        context: AgentContext,
        user_message: str,
        history: tuple[ConversationTurn, ...],
        facts: tuple[VerifiedFact, ...],
    ) -> tuple[AgentContext, RouteResult | None, Engine, EngineResult, str | None]:
        """The turn's result, with the context updated to the routed language."""
        verdict = self.guardrail.check_input(context, user_message)
        if not verdict.allowed:
            blocked = EngineResult(reply=message("input_blocked", context.language), status="blocked")
            return context, None, "out_of_scope", blocked, verdict.reason or "input_guardrail"

        route = self.router.route(context, user_message)
        context = context.model_copy(update={"language": route.language})

        # Escalation triggers and the sensitive-data policy never reach a model.
        if route.credit_decision or route.engine == "escalation":
            reason = "credit_decision" if route.credit_decision else "human_requested"
            handoff = self.escalation.escalate(context, reason, user_message, history, facts)
            return context, route, "escalation", handoff, None
        if route.sensitive_request:
            declined = EngineResult(reply=message("sensitive_request", context.language), status="declined")
            return context, route, route.engine, declined, None

        if route.engine == "inquiry":
            result = self.inquiry.handle(context, user_message)
        elif route.engine == "recommendation":
            result = self.recommendation.handle(context, user_message)
        else:
            result = EngineResult(reply=message("out_of_scope", context.language), status="out_of_scope")
        screened, blocked_reason = self._screen_output(context, result)
        return context, route, route.engine, screened, blocked_reason

    def _screen_output(self, context: AgentContext, result: EngineResult) -> tuple[EngineResult, str | None]:
        scan = scan_output(result.reply)
        verdict = self.guardrail.check_output(context, result.reply)
        if scan.allowed and verdict.allowed:
            return result, None
        reason = scan.reason or verdict.reason or "output_guardrail"
        logger.warning("Reply blocked by output screening: %s", reason)
        blocked = result.model_copy(
            update={"reply": message("output_blocked", context.language), "status": "blocked", "failed": True}
        )
        return blocked, reason
