"""Input/output guardrail abstraction.

BedrockGuardrail calls ApplyGuardrail with source="INPUT" for check_input and
source="OUTPUT" for check_output. The deterministic output scan in agents.safety
runs in addition to whatever guardrail is configured; NoOpGuardrail is the default
for development and tests.
"""

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from agents.context import AgentContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GuardrailVerdict:
    allowed: bool
    reason: str | None = None


ALLOWED = GuardrailVerdict(allowed=True)
# The guardrail could not be reached. Unlike an intervention, this is a system fault: the
# turn is blocked and counts toward the two-failure escalation (see the orchestrator).
GUARDRAIL_UNAVAILABLE = "guardrail_unavailable"


class Guardrail(Protocol):
    def check_input(self, context: AgentContext, text: str) -> GuardrailVerdict: ...

    def check_output(self, context: AgentContext, text: str) -> GuardrailVerdict: ...


class NoOpGuardrail:
    """Allows everything; for local development and tests, or when no guardrail is configured."""

    def check_input(self, context: AgentContext, text: str) -> GuardrailVerdict:
        return ALLOWED

    def check_output(self, context: AgentContext, text: str) -> GuardrailVerdict:
        return ALLOWED


class BedrockGuardrail:
    """A published Bedrock guardrail, applied with ApplyGuardrail (no model call involved).

    Any action other than NONE blocks: the guardrail blocks rather than masks, so
    there is no masked text to show instead. If the call fails, the text is blocked
    too (fail closed) with reason GUARDRAIL_UNAVAILABLE.
    """

    def __init__(self, client: Any, guardrail_id: str, guardrail_version: str):
        self.client = client
        self.guardrail_id = guardrail_id
        self.guardrail_version = guardrail_version

    def check_input(self, context: AgentContext, text: str) -> GuardrailVerdict:
        return self._apply("INPUT", text)

    def check_output(self, context: AgentContext, text: str) -> GuardrailVerdict:
        return self._apply("OUTPUT", text)

    def _apply(self, source: Literal["INPUT", "OUTPUT"], text: str) -> GuardrailVerdict:
        try:
            response = self.client.apply_guardrail(
                guardrailIdentifier=self.guardrail_id,
                guardrailVersion=self.guardrail_version,
                source=source,
                content=[{"text": {"text": text}}],
                # Only what intervened comes back; the text itself is never needed here.
                outputScope="INTERVENTIONS",
            )
        except Exception:
            logger.warning("Bedrock guardrail call failed on %s; blocking", source, exc_info=True)
            return GuardrailVerdict(allowed=False, reason=GUARDRAIL_UNAVAILABLE)

        if response.get("action") == "NONE":
            return ALLOWED
        # Never log the text: it may hold exactly what the guardrail objected to.
        logger.info("Bedrock guardrail intervened on %s: %s", source, response.get("actionReason"))
        return GuardrailVerdict(allowed=False, reason=f"bedrock_guardrail_{source.lower()}")
