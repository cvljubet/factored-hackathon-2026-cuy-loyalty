from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.sessions import EscalationReason, VerifiedFact

ResultStatus = Literal["answered", "unavailable", "declined", "blocked", "failed", "escalated", "out_of_scope"]


class EngineResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    reply: str
    status: ResultStatus
    # Counts toward the two-consecutive-failures escalation rule.
    failed: bool = False
    handoff_id: str | None = None
    handoff_reason: EscalationReason | None = None
    # Trace only: tools the model called and model requests made during the turn.
    tools_called: tuple[str, ...] = ()
    model_requests: int = Field(default=0, ge=0)
    # Trace only: token usage, the models that answered, and whether a fallback model took over.
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    models_used: tuple[str, ...] = ()
    fallback_used: bool = False
    # Successful tool results from this turn, kept in the session for a possible handoff.
    facts: tuple[VerifiedFact, ...] = ()

    @property
    def escalated(self) -> bool:
        return self.status == "escalated"
