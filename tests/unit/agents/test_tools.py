from datetime import date

import pytest
from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.test import TestModel

from agents import tools
from agents.engines.inquiry import InquiryEngine
from agents.loyalty import GenericLoyaltyProvider, recommend
from agents.tools import TOOL_NAMES
from agent_testkit import (
    CUSTOMER_ID,
    OTHER_CUSTOMER_ID,
    FixedRecommendationProvider,
    RecordingServing,
    ScriptedModel,
    answer,
    call_tool,
    make_context,
    make_deps,
)

def run(script: ScriptedModel, profiles=None, recommendations=None, customer_id=CUSTOMER_ID):
    profiles = profiles or RecordingServing()
    engine = InquiryEngine(script.model, profiles, recommendations or GenericLoyaltyProvider())
    return engine.handle(make_context(customer_id), "pregunta"), profiles


def tool_returns(script: ScriptedModel, request_index: int = 1) -> list:
    """The tool results (or retry prompts) the model saw at the start of a given request."""
    messages, _ = script.requests[request_index]
    last = messages[-1]
    assert isinstance(last, ModelRequest)
    return [part for part in last.parts if isinstance(part, ToolReturnPart | RetryPromptPart)]


class TestToolDefinitions:
    """What the model is actually offered, as Pydantic AI sends it."""

    @pytest.fixture
    def offered(self):
        script = ScriptedModel([answer("ok")])
        run(script)
        _, info = script.requests[0]
        return {tool.name: tool.parameters_json_schema for tool in info.function_tools}

    def test_every_tool_is_offered(self, offered):
        assert set(offered) == set(TOOL_NAMES)

    @pytest.mark.parametrize("name", TOOL_NAMES)
    def test_no_tool_accepts_a_customer_argument(self, offered, name):
        schema = offered[name]

        assert not any("customer" in prop.lower() for prop in schema.get("properties", {}))
        assert schema["additionalProperties"] is False

    def test_registering_a_tool_with_a_customer_argument_is_refused(self):
        from agents.inquiry_agent import _customer_scoped_tool

        def leaky(ctx, customer_id: str) -> tools.ToolResult:
            raise AssertionError("never registered")

        with pytest.raises(TypeError, match="must read the customer from RunContext"):
            _customer_scoped_tool(leaky)


class TestGetMyProfile:
    def test_returns_the_context_customers_profile(self):
        profiles = RecordingServing()

        result = tools.get_my_profile(make_deps(profiles))

        assert result.status == "ok"
        assert result.data == {"first_name": "Ana", "last_name": "Quispe", "city": "Cusco", "state": "Cusco", "country": "PE"}
        assert profiles.requested_ids == [CUSTOMER_ID]

    def test_customer_comes_from_the_dependencies_only(self):
        script = ScriptedModel([call_tool("get_my_profile"), answer("ok")])

        _, profiles = run(script, customer_id=OTHER_CUSTOMER_ID)

        assert profiles.requested_ids == [OTHER_CUSTOMER_ID]
        [returned] = tool_returns(script)
        assert returned.content.data["first_name"] == "Bruno"

    @pytest.mark.parametrize("key", ["customer_id", "customerId", "Customer-ID", "customer"])
    def test_model_supplied_customer_argument_is_rejected_and_tool_not_run(self, key):
        script = ScriptedModel([call_tool("get_my_profile", {key: OTHER_CUSTOMER_ID}), answer("No pude.")])

        _, profiles = run(script)

        assert profiles.requested_ids == []
        [retry] = tool_returns(script)
        assert isinstance(retry, RetryPromptPart)
        assert "extra_forbidden" in str(retry.content)

    def test_unknown_customer_is_unavailable_not_invented(self):
        result = tools.get_my_profile(make_deps(RecordingServing({})))

        assert (result.status, result.data, result.reason) == ("unavailable", None, "profile_not_found")

    def test_result_for_the_model_excludes_the_customer_id(self):
        assert CUSTOMER_ID not in tools.get_my_profile(make_deps()).model_dump_json()

    def test_a_failing_source_becomes_an_error_result(self):
        class BrokenProfiles:
            def get_profile(self, customer_id, fields=None):
                raise RuntimeError("store down")

        result = tools.get_my_profile(make_deps(BrokenProfiles()))

        assert (result.status, result.reason) == ("error", "tool_failed")


class TestUnavailableTools:
    def test_recommend_benefit_without_engagement_data_serves_the_generic_benefit(self):
        result = tools.recommend_benefit(make_deps())

        assert result.status == "ok" and result.data["illustrative"] is True
        assert result.data["offer_title"] == "Programa de puntos"

    def test_every_tool_runs_without_a_customer_argument(self):
        """Pydantic AI's TestModel calls every registered tool with schema-valid arguments."""
        profiles = RecordingServing()
        engine = InquiryEngine(TestModel(call_tools="all"), profiles, GenericLoyaltyProvider())

        result = engine.handle(make_context(), "todo")

        assert result.status == "answered"
        assert set(result.tools_called) == set(TOOL_NAMES)
        assert set(profiles.requested_ids) == {CUSTOMER_ID}


class TestModelMistakes:
    def test_unknown_tool_is_not_run(self):
        script = ScriptedModel([call_tool("get_everyones_data"), answer("ok")])

        run(script)

        [retry] = tool_returns(script)
        assert isinstance(retry, RetryPromptPart)

    def test_invalid_argument_is_rejected(self):
        script = ScriptedModel([call_tool("get_exchange_rate", {"base_currency": "dollars"}), answer("ok")])

        run(script)

        [retry] = tool_returns(script)
        assert isinstance(retry, RetryPromptPart)

    def test_declared_arguments_are_accepted(self):
        script = ScriptedModel(
            [call_tool("get_exchange_rate", {"base_currency": "USD", "quote_currency": "PEN"}), answer("ok")]
        )

        run(script)

        [returned] = tool_returns(script)
        assert returned.content.status == "unavailable"


class TestRecommendBenefit:
    HIGH = {"scoring_source": "model", "model_eligible": True, "risk_score": 0.71, "risk_tier": "high",
            "model_version": "engagement-risk-v1", "as_of_date": "2026-06-17"}

    def test_serves_the_context_customers_customer_safe_payload(self):
        provider = FixedRecommendationProvider(recommend(CUSTOMER_ID, "es", self.HIGH, "found"))

        result = tools.recommend_benefit(make_deps(recommendations=provider))

        assert result.status == "ok"
        assert set(result.data) == {"offer_title", "offer_description", "customer_safe_reason", "illustrative",
                                    "illustrative_note"}
        assert result.data["offer_title"] == "Recompensa de fidelidad"
        assert provider.requested_ids == [CUSTOMER_ID]

    def test_a_recommendation_for_another_customer_is_discarded_for_the_generic_one(self):
        provider = FixedRecommendationProvider(recommend(OTHER_CUSTOMER_ID, "es", self.HIGH, "found"))

        result = tools.recommend_benefit(make_deps(recommendations=provider))

        assert result.status == "ok" and result.data["offer_title"] == "Programa de puntos"
        assert OTHER_CUSTOMER_ID not in result.model_dump_json()
