"""Handoff to a human agent.

Triggers (decided by the orchestrator): an explicit request for a human, two
consecutive failed turns, and any individual credit or eligibility decision.
The customer gets a fixed acknowledgement; the conversation context goes into a
Handoff record for the human-service workflow. No human reply is simulated.
"""

import threading
import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agents.context import AgentContext, Language
from agents.engines.base import EngineResult
from agents.messages import message
from agents.safety import redact
from agents.sessions import ConversationTurn, EscalationReason, VerifiedFact


class Handoff(BaseModel):
    """What a human agent receives. A persistent store saves it as one item (its JSON).

    Holds only customer-visible conversation text (redacted) and tool data the
    assistant already showed or could show; never tokens or backend-only fields.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    customer_id: str
    session_id: str
    reason: EscalationReason
    language: Language
    status: Literal["pending"] = "pending"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # The message that triggered the handoff: what the customer still needs answered.
    open_question: str
    # How the conversation started, even when it is no longer among the recent turns.
    opening_message: ConversationTurn | None = None
    recent_turns: tuple[ConversationTurn, ...] = ()
    verified_facts: tuple[VerifiedFact, ...] = ()


class HandoffStore(Protocol):
    def create(self, handoff: Handoff) -> None: ...


class InMemoryHandoffStore:
    """Process-local handoff queue for development and the demo; lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handoffs: list[Handoff] = []

    def create(self, handoff: Handoff) -> None:
        with self._lock:
            self._handoffs.append(handoff)

    @property
    def handoffs(self) -> list[Handoff]:
        with self._lock:
            return list(self._handoffs)


class EscalationEngine:
    def __init__(self, store: HandoffStore):
        self.store = store

    def escalate(
        self,
        context: AgentContext,
        reason: EscalationReason,
        open_question: str,
        recent_turns: tuple[ConversationTurn, ...] = (),
        verified_facts: tuple[VerifiedFact, ...] = (),
        opening: ConversationTurn | None = None,
    ) -> EngineResult:
        handoff = Handoff(
            id=f"HO-{uuid.uuid4().hex[:10].upper()}",
            customer_id=context.customer_id,
            session_id=context.session_id,
            reason=reason,
            language=context.language,
            open_question=redact(open_question),
            opening_message=opening,
            recent_turns=recent_turns,
            verified_facts=verified_facts,
        )
        self.store.create(handoff)
        return EngineResult(
            reply=message("handoff_acknowledgement", context.language),
            status="escalated",
            handoff_id=handoff.id,
            handoff_reason=reason,
        )
