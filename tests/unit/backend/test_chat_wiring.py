import pytest
from pydantic import ValidationError
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.function import FunctionModel

from agents.guardrails import BedrockGuardrail, NoOpGuardrail
from agents.model_router import HybridRouter
from agents.models import bedrock_runtime_client
from agents.routing import RuleBasedRouter
from app.chat.dependencies import bedrock_config, build_guardrail, build_inquiry_model, build_router, uses_bedrock
from app.config import Settings

BASE = {"cognito_region": "us-east-2", "cognito_user_pool_id": "us-east-2_x", "cognito_app_client_id": "c"}


def settings(**overrides) -> Settings:
    """Settings from arguments and the test environment only, never a developer's .env."""
    return Settings(_env_file=None, **BASE, **overrides)


def test_defaults_need_no_aws_access():
    defaults = settings()

    assert isinstance(build_inquiry_model(defaults), FunctionModel)
    assert isinstance(build_router(defaults), RuleBasedRouter)


def test_bedrock_settings_select_converse_models_without_calling_aws():
    bedrock = settings(agent_llm="bedrock", agent_router="bedrock")

    model = build_inquiry_model(bedrock)

    assert isinstance(model, BedrockConverseModel)
    assert model.model_name == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert isinstance(build_router(bedrock), HybridRouter)


def test_guardrail_is_off_unless_configured():
    plain = settings()

    assert isinstance(build_guardrail(plain), NoOpGuardrail)
    assert uses_bedrock(plain) is False


def test_configured_guardrail_uses_the_shared_client():
    configured = settings(bedrock_guardrail_id="gr-1", bedrock_guardrail_version="2")
    client = bedrock_runtime_client(bedrock_config(configured))

    guardrail = build_guardrail(configured, client)

    assert isinstance(guardrail, BedrockGuardrail)
    assert (guardrail.client, guardrail.guardrail_id, guardrail.guardrail_version) == (client, "gr-1", "2")
    assert uses_bedrock(configured) is True


def test_half_a_guardrail_configuration_is_refused():
    with pytest.raises(ValidationError, match="BEDROCK_GUARDRAIL_VERSION"):
        settings(bedrock_guardrail_id="gr-1")


def test_empty_environment_variables_leave_optional_features_off(monkeypatch):
    for name in (
        "BEDROCK_INQUIRY_FALLBACK_MODEL_ID",
        "BEDROCK_GUARDRAIL_ID",
        "BEDROCK_GUARDRAIL_VERSION",
        "BEDROCK_PROFILE",
    ):
        monkeypatch.setenv(name, "")

    empty = settings(agent_llm="bedrock")

    assert isinstance(build_inquiry_model(empty), BedrockConverseModel)  # no FallbackModel around ""
    assert (empty.bedrock_profile, empty.bedrock_guardrail_enabled) == (None, False)


def test_bedrock_settings_reach_the_client():
    configured = settings(agent_llm="bedrock", bedrock_read_timeout_seconds=9, bedrock_max_attempts=1)

    model = build_inquiry_model(configured)

    assert model.client.meta.config.read_timeout == 9
    assert model.client.meta.config.retries["total_max_attempts"] == 1
