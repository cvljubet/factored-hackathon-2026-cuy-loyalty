"""One chat turn: guardrails, routing, engine dispatch, output safety and escalation.

This is deliberately plain application code, not part of any LLM agent: the
safety policy, failure counting and escalation must behave the same whichever
model is plugged in.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel, ConfigDict, Field

from agents.context import DEFAULT_LANGUAGE, AgentContext, Language
from agents.engines.base import EngineResult, ResultStatus
from agents.engines.escalation import EscalationEngine
from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import RecommendationEngine
from agents.guardrails import GUARDRAIL_UNAVAILABLE, Guardrail
from agents.messages import message
from agents.routing import Engine, RouteResult, Router
from agents.safety import redact, scan_output
from agents.sessions import MAX_TURNS, ConversationTurn, SessionStore, VerifiedFact, merge_facts

logger = logging.getLogger(__name__)

FAILURES_BEFORE_ESCALATION = 2


class TurnTrace(BaseModel):
    """How a turn was handled, for logs and evaluation (not sent to the client)."""

    model_config = ConfigDict(frozen=True)

    route: RouteResult | None = None
    tools_called: tuple[str, ...] = ()
    model_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    models_used: tuple[str, ...] = ()
    fallback_used: bool = False
    blocked_reason: str | None = None
    consecutive_failures: int = Field(default=0, ge=0)
    # Milliseconds per stage that ran: guardrail_input, router, engine, guardrail_output.
    stage_ms: dict[str, float] = Field(default_factory=dict)


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
        customer_turn = ConversationTurn(role="customer", text=redact(user_message))
        history = (*state.turns, customer_turn)[-MAX_TURNS:]
        opening = state.opening or customer_turn

        stage_ms: dict[str, float] = {}
        context, route, engine, result, blocked_reason = self._run(
            context, user_message, history, state.facts, opening, stage_ms
        )
        trace_from = result
        facts = merge_facts(state.facts, result.facts)

        failures = context.consecutive_failures + 1 if result.failed else 0
        if result.failed and failures >= FAILURES_BEFORE_ESCALATION:
            engine = "escalation"
            result = self.escalation.escalate(context, "repeated_failures", user_message, history, facts, opening)
        handoff_state = {}
        if result.escalated:
            failures = 0
            handoff_state = {
                "handoff_count": state.handoff_count + 1,
                "last_handoff_id": result.handoff_id,
                "last_handoff_reason": result.handoff_reason,
            }

        assistant_turn = ConversationTurn(role="assistant", text=redact(result.reply))
        turns = (*history, assistant_turn)[-MAX_TURNS:]
        saved = state.model_copy(
            update={
                "version": state.version + 1,
                "consecutive_failures": failures,
                "language": context.language,
                "turns": turns,
                "facts": facts,
                "opening": opening,
                **handoff_state,
            }
        )
        # Raises SessionConflict if another request saved this session meanwhile (e.g. a second tab).
        self.sessions.save(customer_id, session_id, saved, new_turns=(customer_turn, assistant_turn))
        trace = TurnTrace(
            route=route,
            tools_called=trace_from.tools_called,
            model_requests=trace_from.model_requests,
            input_tokens=trace_from.input_tokens,
            output_tokens=trace_from.output_tokens,
            models_used=trace_from.models_used,
            fallback_used=trace_from.fallback_used,
            blocked_reason=blocked_reason,
            consecutive_failures=failures,
            stage_ms=stage_ms,
        )
        # The structured per-turn record is written by the caller (the backend's chat route).
        logger.debug(
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
        opening: ConversationTurn,
        stage_ms: dict[str, float],
    ) -> tuple[AgentContext, RouteResult | None, Engine, EngineResult, str | None]:
        """The turn's result, with the context updated to the routed language; fills stage_ms."""
        with _timed(stage_ms, "guardrail_input"):
            verdict = self.guardrail.check_input(context, user_message)
        if not verdict.allowed:
            # An intervention (e.g. a prompt attack) is not a failure to help, so it never escalates.
            # An unreachable guardrail is: two in a row hand the customer to a human.
            unavailable = verdict.reason == GUARDRAIL_UNAVAILABLE
            blocked = EngineResult(
                reply=message("input_blocked", context.language), status="blocked", failed=unavailable
            )
            return context, None, "out_of_scope", blocked, verdict.reason or "input_guardrail"

        with _timed(stage_ms, "router"):
            route = self.router.route(context, user_message)
        context = context.model_copy(update={"language": route.language})

        # Escalation triggers and the sensitive-data policy never reach a model.
        if route.credit_decision or route.engine == "escalation":
            reason = "credit_decision" if route.credit_decision else "human_requested"
            with _timed(stage_ms, "engine"):
                handoff = self.escalation.escalate(context, reason, user_message, history, facts, opening)
            return context, route, "escalation", handoff, None
        if route.sensitive_request:
            declined = EngineResult(reply=message("sensitive_request", context.language), status="declined")
            return context, route, route.engine, declined, None

        with _timed(stage_ms, "engine"):
            if route.engine == "inquiry":
                result = self.inquiry.handle(context, user_message)
            elif route.engine == "recommendation":
                result = self.recommendation.handle(context, user_message)
            else:
                result = EngineResult(reply=message("out_of_scope", context.language), status="out_of_scope")
        with _timed(stage_ms, "guardrail_output"):
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


@contextmanager
def _timed(stage_ms: dict[str, float], stage: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        stage_ms[stage] = round((time.perf_counter() - start) * 1000, 1)
