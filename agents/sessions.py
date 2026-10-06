import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agents.context import Language

# How much conversation a session keeps (and a handoff carries) for a human agent: 10 exchanges.
MAX_TURNS = 20
MAX_FACTS = 10

EscalationReason = Literal["human_requested", "repeated_failures", "credit_decision"]


def _now() -> datetime:
    return datetime.now(UTC)


class ConversationTurn(BaseModel):
    """One message in the session, with credentials and full numbers already redacted."""

    model_config = ConfigDict(frozen=True)

    role: Literal["customer", "assistant"]
    text: str
    at: datetime = Field(default_factory=_now)
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)


class VerifiedFact(BaseModel):
    """Data a tool returned successfully during the session (what the assistant actually knew)."""

    model_config = ConfigDict(frozen=True)

    tool: str
    data: dict[str, Any]
    retrieved_at: datetime = Field(default_factory=_now)


class SessionState(BaseModel):
    """What the orchestrator carries from one turn to the next."""

    model_config = ConfigDict(frozen=True)

    # Saves made so far; each save must be exactly one more than the state it was loaded from.
    version: int = Field(default=0, ge=0)
    consecutive_failures: int = Field(default=0, ge=0)
    language: Language | None = None
    turns: tuple[ConversationTurn, ...] = ()
    facts: tuple[VerifiedFact, ...] = ()
    # The conversation's first customer message, kept when older turns are trimmed.
    opening: ConversationTurn | None = None
    handoff_count: int = Field(default=0, ge=0)
    last_handoff_id: str | None = None
    last_handoff_reason: EscalationReason | None = None


class SessionConflict(Exception):
    """The session was saved by another request since this one loaded it; this turn was not saved."""


def merge_facts(existing: tuple[VerifiedFact, ...], new: tuple[VerifiedFact, ...]) -> tuple[VerifiedFact, ...]:
    """The latest result per tool, most recent last, capped at MAX_FACTS."""
    latest = {fact.tool: fact for fact in (*existing, *new)}
    return tuple(sorted(latest.values(), key=lambda fact: fact.retrieved_at))[-MAX_FACTS:]


class SessionStore(Protocol):
    """Session state, keyed by customer and session so one customer never sees another's."""

    def load(self, customer_id: str, session_id: str) -> SessionState: ...

    def save(
        self, customer_id: str, session_id: str, state: SessionState, new_turns: tuple[ConversationTurn, ...]
    ) -> None:
        """Save state (whose version is one more than the one loaded) and this turn's new_turns.

        Raises SessionConflict when another save of the session happened in between.
        """
        ...


class InMemorySessionStore:
    """Process-local session state for development; lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[tuple[str, str], SessionState] = {}

    def load(self, customer_id: str, session_id: str) -> SessionState:
        with self._lock:
            return self._states.get((customer_id, session_id), SessionState())

    def save(
        self, customer_id: str, session_id: str, state: SessionState, new_turns: tuple[ConversationTurn, ...]
    ) -> None:
        # state.turns is already the window to keep, new_turns included.
        with self._lock:
            current = self._states.get((customer_id, session_id))
            if current is not None and state.version != current.version + 1:
                raise SessionConflict(f"session {session_id} was saved since it was loaded")
            self._states[(customer_id, session_id)] = state
