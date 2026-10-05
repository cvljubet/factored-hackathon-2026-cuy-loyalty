"""Input/output guardrail abstraction.

A Bedrock implementation will call ApplyGuardrail with source="INPUT" for
check_input and source="OUTPUT" for check_output, and block when the action is
GUARDRAIL_INTERVENED. The deterministic output scan in agents.safety runs in
addition to whatever guardrail is configured.
"""

from dataclasses import dataclass
from typing import Protocol

from agents.context import AgentContext


@dataclass(frozen=True)
class GuardrailVerdict:
    allowed: bool
    reason: str | None = None


ALLOWED = GuardrailVerdict(allowed=True)


class Guardrail(Protocol):
    def check_input(self, context: AgentContext, text: str) -> GuardrailVerdict: ...

    def check_output(self, context: AgentContext, text: str) -> GuardrailVerdict: ...


class NoOpGuardrail:
    """Allows everything; stands in until Bedrock Guardrails is provisioned."""

    def check_input(self, context: AgentContext, text: str) -> GuardrailVerdict:
        return ALLOWED

    def check_output(self, context: AgentContext, text: str) -> GuardrailVerdict:
        return ALLOWED
