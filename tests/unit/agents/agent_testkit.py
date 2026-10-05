"""Shared fakes for the agents tests (synthetic data only, no model access)."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic_ai import models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.context import AgentContext, Language
from agents.deps import AgentDeps
from agents.engines.recommendation import NotReadyRecommendationProvider, RecommendationPayload, RecommendationProvider
from agents.serving import InMemoryServingRepository

# Any attempt to reach a real model (e.g. Bedrock) fails the test instead of calling AWS.
models.ALLOW_MODEL_REQUESTS = False

CUSTOMER_ID = "CUST-A"
OTHER_CUSTOMER_ID = "CUST-B"

PROFILES = {
    CUSTOMER_ID: {
        "customer_id": CUSTOMER_ID,
        "first_name": "Ana",
        "last_name": "Quispe",
        "city": "Cusco",
        "state": "Cusco",
        "country": "PE",
    },
    OTHER_CUSTOMER_ID: {
        "customer_id": OTHER_CUSTOMER_ID,
        "first_name": "Bruno",
        "last_name": "Other",
        "city": "Recife",
        "state": "Pernambuco",
        "country": "BR",
    },
}


class RecordingServing(InMemoryServingRepository):
    """In-memory serving data that records which customer every customer lookup was for."""

    def __init__(self, profiles: Mapping[str, Mapping[str, Any]] = PROFILES, **data: Any):
        super().__init__(profiles, **data)
        self.requested_ids: list[str] = []

    def get_profile(self, customer_id: str, fields: Sequence[str] | None = None) -> Mapping[str, Any] | None:
        self.requested_ids.append(customer_id)
        return super().get_profile(customer_id, fields)

    def _events(self, kind: str, customer_id: str, limit: int) -> list[dict[str, Any]]:
        self.requested_ids.append(customer_id)
        return super()._events(kind, customer_id, limit)


class FixedRecommendationProvider:
    def __init__(self, payload: RecommendationPayload | None):
        self.payload = payload
        self.requested_ids: list[str] = []

    def get_recommendations(self, customer_id: str) -> RecommendationPayload | None:
        self.requested_ids.append(customer_id)
        return self.payload


class ScriptedModel:
    """A Pydantic AI FunctionModel that replays a script and records what the model was shown.

    Each step is a ModelResponse to return or an exception to raise.
    """

    def __init__(self, steps: Iterable[ModelResponse | Exception] = ()):
        self._steps = list(steps)
        self.requests: list[tuple[list[ModelMessage], AgentInfo]] = []
        self.model = FunctionModel(self._respond, model_name="scripted")

    def _respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append((list(messages), info))
        if not self._steps:
            raise AssertionError("Scripted model has no more responses")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def make_context(customer_id: str = CUSTOMER_ID, language: Language = "es", failures: int = 0) -> AgentContext:
    return AgentContext(customer_id=customer_id, session_id="session-1", language=language, consecutive_failures=failures)


def make_deps(
    serving: Any = None,
    recommendations: RecommendationProvider | None = None,
    **context: Any,
) -> AgentDeps:
    return AgentDeps(
        context=make_context(**context),
        serving=serving or RecordingServing(),
        recommendations=recommendations or NotReadyRecommendationProvider(),
    )


def call_tool(name: str, arguments: Mapping[str, Any] | None = None, call_id: str = "call-1") -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, dict(arguments or {}), tool_call_id=call_id)])


def answer(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def model_down() -> ModelHTTPError:
    """What a provider outage (e.g. Bedrock throttling) looks like to the agent."""
    return ModelHTTPError(status_code=503, model_name="scripted", body="unavailable")
