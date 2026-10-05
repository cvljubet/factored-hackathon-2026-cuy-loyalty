from datetime import date

import pytest
from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.models.test import TestModel

from agents import tools
from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import NotReadyRecommendationProvider, ProductRecommendation, RecommendationPayload
from agents.tools import TOOL_NAMES
from agent_testkit import (
    CUSTOMER_ID,
    OTHER_CUSTOMER_ID,
    FixedRecommendationProvider,
    RecordingProfileSource,
    ScriptedModel,
    answer,
    call_tool,
    make_context,
    make_deps,
)

NOT_READY_TOOLS = sorted(set(TOOL_NAMES) - {"get_my_profile", "recommend_products"})


def run(script: ScriptedModel, profiles=None, recommendations=None, customer_id=CUSTOMER_ID):
    profiles = profiles or RecordingProfileSource()
    engine = InquiryEngine(script.model, profiles, recommendations or NotReadyRecommendationProvider())
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
        profiles = RecordingProfileSource()

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
        result = tools.get_my_profile(make_deps(RecordingProfileSource({})))

        assert (result.status, result.data, result.reason) == ("unavailable", None, "profile_not_found")

    def test_result_for_the_model_excludes_the_customer_id(self):
        assert CUSTOMER_ID not in tools.get_my_profile(make_deps()).model_dump_json()

    def test_a_failing_source_becomes_an_error_result(self):
        class BrokenProfiles:
            def get_profile(self, customer_id):
                raise RuntimeError("store down")

        result = tools.get_my_profile(make_deps(BrokenProfiles()))

        assert (result.status, result.reason) == ("error", "tool_failed")


class TestUnavailableTools:
    @pytest.mark.parametrize("name", NOT_READY_TOOLS)
    def test_reports_not_ready_without_data(self, name):
        script = ScriptedModel([call_tool(name), answer("No disponible.")])

        result, profiles = run(script)

        [returned] = tool_returns(script)
        assert returned.content == tools.ToolResult(tool=name, status="unavailable", reason="data_source_not_ready")
        assert result.status == "answered"
        assert profiles.requested_ids == []

    def test_recommend_products_is_unavailable_until_the_model_exists(self):
        result = tools.recommend_products(make_deps())

        assert (result.status, result.reason, result.data) == ("unavailable", "model_not_ready", None)

    def test_every_tool_runs_without_a_customer_argument(self):
        """Pydantic AI's TestModel calls every registered tool with schema-valid arguments."""
        profiles = RecordingProfileSource()
        engine = InquiryEngine(TestModel(call_tools="all"), profiles, NotReadyRecommendationProvider())

        result = engine.handle(make_context(), "todo")

        assert result.status == "answered"
        assert set(result.tools_called) == set(TOOL_NAMES)
        assert profiles.requested_ids == [CUSTOMER_ID]


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


class TestRecommendProducts:
    def payload(self, customer_id: str) -> RecommendationPayload:
        return RecommendationPayload(
            customer_id=customer_id,
            model_version="test-v0",
            as_of=date(2026, 10, 1),
            recommendations=[ProductRecommendation(product_type="travel_card", score=0.8, rank=1, top_categories=["travel"])],
        )

    def test_serves_the_context_customers_payload(self):
        provider = FixedRecommendationProvider(self.payload(CUSTOMER_ID))

        result = tools.recommend_products(make_deps(recommendations=provider))

        assert result.status == "ok"
        assert result.data["recommendations"][0]["product_type"] == "travel_card"
        assert "customer_id" not in result.data
        assert provider.requested_ids == [CUSTOMER_ID]

    def test_refuses_a_payload_for_another_customer(self):
        provider = FixedRecommendationProvider(self.payload(OTHER_CUSTOMER_ID))

        result = tools.recommend_products(make_deps(recommendations=provider))

        assert (result.status, result.data) == ("error", None)
