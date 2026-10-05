import threading
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agents.context import Language

# How much conversation a session keeps (and a handoff carries) for a human agent.
MAX_TURNS = 10
MAX_FACTS = 10


def _now() -> datetime:
    return datetime.now(UTC)


class ConversationTurn(BaseModel):
    """One message in the session, with credentials and full numbers already redacted."""

    model_config = ConfigDict(frozen=True)

    role: Literal["customer", "assistant"]
    text: str
    at: datetime = Field(default_factory=_now)


class VerifiedFact(BaseModel):
    """Data a tool returned successfully during the session (what the assistant actually knew)."""

    model_config = ConfigDict(frozen=True)

    tool: str
    data: dict[str, Any]
    retrieved_at: datetime = Field(default_factory=_now)


class SessionState(BaseModel):
    """What the orchestrator carries from one turn to the next."""

    model_config = ConfigDict(frozen=True)

    consecutive_failures: int = Field(default=0, ge=0)
    language: Language | None = None
    turns: tuple[ConversationTurn, ...] = ()
    facts: tuple[VerifiedFact, ...] = ()


def merge_facts(existing: tuple[VerifiedFact, ...], new: tuple[VerifiedFact, ...]) -> tuple[VerifiedFact, ...]:
    """The latest result per tool, most recent last, capped at MAX_FACTS."""
    latest = {fact.tool: fact for fact in (*existing, *new)}
    return tuple(sorted(latest.values(), key=lambda fact: fact.retrieved_at))[-MAX_FACTS:]


class SessionStore(Protocol):
    """Session state, keyed by customer and session so one customer never sees another's."""

    def load(self, customer_id: str, session_id: str) -> SessionState: ...

    def save(self, customer_id: str, session_id: str, state: SessionState) -> None: ...


class InMemorySessionStore:
    """Process-local session state for development; lost on restart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[tuple[str, str], SessionState] = {}

    def load(self, customer_id: str, session_id: str) -> SessionState:
        with self._lock:
            return self._states.get((customer_id, session_id), SessionState())

    def save(self, customer_id: str, session_id: str, state: SessionState) -> None:
        with self._lock:
            self._states[(customer_id, session_id)] = state
