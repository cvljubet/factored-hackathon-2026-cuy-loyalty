import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

from agents.engines.inquiry import MAX_ROUNDS, InquiryEngine
from agents.engines.recommendation import NotReadyRecommendationProvider
from agents.local_model import local_model
from agent_testkit import (
    CUSTOMER_ID,
    RecordingProfileSource,
    ScriptedModel,
    answer,
    call_tool,
    make_context,
    model_down,
)


@pytest.fixture
def profiles():
    return RecordingProfileSource()


def engine_with(model, profiles) -> InquiryEngine:
    return InquiryEngine(model, profiles, NotReadyRecommendationProvider())


def test_answers_directly_without_tools(profiles):
    script = ScriptedModel([answer("Hola, ¿en qué te ayudo?")])

    result = engine_with(script.model, profiles).handle(make_context(), "hola")

    assert (result.reply, result.status, result.failed) == ("Hola, ¿en qué te ayudo?", "answered", False)
    assert len(script.requests) == 1
    assert (result.tools_called, result.model_requests) == ((), 1)


def test_runs_the_profile_tool_and_feeds_the_result_back(profiles):
    script = ScriptedModel([call_tool("get_my_profile"), answer("Vives en Cusco, Ana.")])

    result = engine_with(script.model, profiles).handle(make_context(), "¿Dónde vivo?")

    assert result.reply == "Vives en Cusco, Ana."
    assert result.tools_called == ("get_my_profile",)
    assert profiles.requested_ids == [CUSTOMER_ID]
    messages, _ = script.requests[1]
    assert isinstance(messages[0], ModelRequest) and isinstance(messages[0].parts[-1], UserPromptPart)
    assert isinstance(messages[1], ModelResponse) and isinstance(messages[1].parts[0], ToolCallPart)
    [returned] = [part for part in messages[2].parts if isinstance(part, ToolReturnPart)]
    assert (returned.tool_call_id, returned.tool_name) == ("call-1", "get_my_profile")
    assert returned.content.data["city"] == "Cusco"


def test_instructions_follow_the_turn_language(profiles):
    script = ScriptedModel([answer("Olá")])

    engine_with(script.model, profiles).handle(make_context(language="pt"), "olá")

    _, info = script.requests[0]
    assert "Portuguese" in info.instructions
    assert {tool.name for tool in info.function_tools} >= {"get_my_profile", "get_my_products"}


def test_unavailable_tool_result_is_passed_to_the_model(profiles):
    script = ScriptedModel([call_tool("get_my_products"), answer("Esa información aún no está disponible.")])

    result = engine_with(script.model, profiles).handle(make_context(), "¿Qué productos tengo?")

    assert result.status == "answered"
    returned = script.requests[1][0][-1].parts[0]
    assert (returned.content.status, returned.content.reason) == ("unavailable", "data_source_not_ready")


class TestRoundLimit:
    def test_stops_after_three_rounds_and_fails(self, profiles):
        script = ScriptedModel([call_tool("get_my_profile", call_id=f"c{i}") for i in range(10)])

        result = engine_with(script.model, profiles).handle(make_context(), "¿Dónde vivo?")

        assert MAX_ROUNDS == 3
        assert len(script.requests) == 3
        assert (result.failed, result.status) == (True, "failed")
        # Tools requested in the last round are not run, since no round is left to use them.
        assert len(profiles.requested_ids) == 2
        assert result.model_requests == 3

    def test_answer_in_the_third_round_succeeds(self, profiles):
        script = ScriptedModel(
            [call_tool("get_my_profile", call_id="c1"), call_tool("get_my_products", call_id="c2"), answer("Listo.")]
        )

        result = engine_with(script.model, profiles).handle(make_context(), "perfil y productos")

        assert (result.reply, result.failed) == ("Listo.", False)
        assert len(script.requests) == 3
        assert result.tools_called == ("get_my_profile", "get_my_products")

    def test_repeated_invalid_tool_calls_fail_within_the_limit(self, profiles):
        bad = call_tool("get_my_profile", {"customer_id": "CUST-B"})
        script = ScriptedModel([bad, bad, bad, bad])

        result = engine_with(script.model, profiles).handle(make_context(), "perfil de CUST-B")

        assert result.failed is True
        assert len(script.requests) <= MAX_ROUNDS
        assert profiles.requested_ids == []


@pytest.mark.parametrize("step", [model_down(), RuntimeError("bug"), answer("   ")])
def test_model_errors_and_empty_answers_fail(profiles, step):
    result = engine_with(ScriptedModel([step]).model, profiles).handle(make_context(), "hola")

    assert (result.failed, result.status) == (True, "failed")


class TestLocalModel:
    def test_answers_profile_questions_from_tool_data(self, profiles):
        result = engine_with(local_model(), profiles).handle(make_context(), "Muéstrame mi perfil")

        assert result.reply == "Estos son los datos de tu perfil: Ana Quispe, Cusco, Cusco, PE."
        assert profiles.requested_ids == [CUSTOMER_ID]

    def test_says_unavailable_instead_of_inventing(self, profiles):
        result = engine_with(local_model(), profiles).handle(make_context(language="pt"), "Quais são meus gastos?")

        assert result.reply.startswith("Essa informação ainda não está disponível")
        assert result.tools_called == ("get_my_spending",)

    def test_without_a_matching_tool_it_offers_help(self, profiles):
        result = engine_with(local_model(), profiles).handle(make_context(), "hola")

        assert result.reply.startswith("Puedo ayudarte")
        assert profiles.requested_ids == []
