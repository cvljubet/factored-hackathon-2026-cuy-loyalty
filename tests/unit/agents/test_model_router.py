import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from agents.model_router import HybridRouter, ModelRouter
from agents.routing import RouteResult
from agent_testkit import make_context, model_down


def model_routing_to(**route) -> TestModel:
    """A test model whose structured output is the given route."""
    return TestModel(custom_output_args={"language": "es", "confidence": 0.9, **route})


class TestModelRouter:
    def test_returns_validated_structured_output(self):
        router = ModelRouter(model_routing_to(engine="recommendation"))

        result = router.route(make_context(), "¿Qué me conviene?")

        assert result == RouteResult(engine="recommendation", language="es", confidence=0.9)

    def test_output_schema_is_the_route_result_model(self):
        seen: list[AgentInfo] = []

        def respond(messages, info: AgentInfo) -> ModelResponse:
            seen.append(info)
            args = {"engine": "inquiry", "language": "pt", "confidence": 0.7}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

        result = ModelRouter(FunctionModel(respond)).route(make_context(language="pt"), "Qual é meu saldo?")

        assert result.engine == "inquiry"
        properties = seen[0].output_tools[0].parameters_json_schema["properties"]
        assert set(properties) == {"engine", "language", "confidence", "sensitive_request", "credit_decision"}
        assert "pt" in seen[0].instructions

    def test_invalid_model_output_is_rejected(self):
        def respond(messages, info: AgentInfo) -> ModelResponse:
            bad = {"engine": "chitchat", "language": "en", "confidence": 3}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, bad)])

        with pytest.raises(Exception):
            ModelRouter(FunctionModel(respond)).route(make_context(), "hola")


class TestHybridRouter:
    def test_uses_the_model_route_when_no_rule_fires(self):
        router = HybridRouter(ModelRouter(model_routing_to(engine="inquiry")))

        assert router.route(make_context(), "¿Cuál es mi saldo?").engine == "inquiry"

    @pytest.mark.parametrize(
        "message, flag",
        [("¿Me aprueban un préstamo?", "credit_decision"), ("¿Cuál es mi puntaje de crédito?", "sensitive_request")],
    )
    def test_deterministic_flags_are_ored_in(self, message, flag):
        router = HybridRouter(ModelRouter(model_routing_to(engine="inquiry")))

        assert getattr(router.route(make_context(), message), flag) is True

    def test_human_request_forces_escalation_even_if_the_model_disagrees(self):
        router = HybridRouter(ModelRouter(model_routing_to(engine="out_of_scope")))

        assert router.route(make_context(), "Quiero hablar con un asesor").engine == "escalation"

    def test_credit_decision_forces_escalation(self):
        router = HybridRouter(ModelRouter(model_routing_to(engine="inquiry")))

        result = router.route(make_context(), "¿Soy elegible para un crédito?")

        assert (result.engine, result.credit_decision) == ("escalation", True)

    def test_model_flags_are_kept(self):
        router = HybridRouter(ModelRouter(model_routing_to(engine="escalation", credit_decision=True)))

        result = router.route(make_context(), "una pregunta ambigua")

        assert (result.engine, result.credit_decision) == ("escalation", True)

    def test_falls_back_to_rules_when_the_model_fails(self):
        def respond(messages, info):
            raise model_down()

        router = HybridRouter(ModelRouter(FunctionModel(respond)))

        assert router.route(make_context(), "Quero falar com um atendente").engine == "escalation"
