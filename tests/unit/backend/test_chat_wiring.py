import pytest
from pydantic import ValidationError
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.function import FunctionModel

from agents.conversations import DynamoHandoffStore, DynamoSessionStore
from agents.engines.escalation import InMemoryHandoffStore
from agents.guardrails import BedrockGuardrail, NoOpGuardrail
from agents.model_router import HybridRouter
from agents.models import bedrock_runtime_client
from agents.routing import RuleBasedRouter
from agents.serving import DynamoServingRepository
from agents.sessions import InMemorySessionStore
from app.chat.dependencies import bedrock_config, build_chat_stores, build_guardrail, build_inquiry_model, build_router, uses_bedrock
from app.customers.dependencies import RepositoryProfileSource, build_serving
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


def test_serving_defaults_to_memory_without_aws():
    assert isinstance(build_serving(settings()), RepositoryProfileSource)


def test_dynamodb_serving_settings_reach_the_table():
    serving = build_serving(settings(serving_backend="dynamodb", serving_table_name="t-1", serving_aws_region="us-west-2"))

    assert isinstance(serving, DynamoServingRepository)
    assert (serving.table.name, serving.table.meta.client.meta.region_name) == ("t-1", "us-west-2")


@pytest.fixture
def three_profiles(tmp_path, monkeypatch):
    """An AWS config with one profile per account, and a global AWS_PROFILE naming a third."""
    config = tmp_path / "config"
    config.write_text(
        "".join(
            f"[profile {name}]\naws_access_key_id = {key}\naws_secret_access_key = secret\nregion = us-east-2\n"
            for name, key in [("model-account", "AKIDBEDROCK"), ("team-account", "AKIDTEAM"), ("global", "AKIDGLOBAL")]
        )
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_PROFILE", "global")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")


def access_key(client) -> str:
    return client._request_signer._credentials.get_frozen_credentials().access_key


def test_bedrock_and_dynamodb_use_their_own_profiles(three_profiles):
    both = settings(agent_llm="bedrock", bedrock_profile="model-account",
                    serving_backend="dynamodb", serving_aws_profile="team-account")

    bedrock = bedrock_runtime_client(bedrock_config(both))
    serving = build_serving(both)

    assert access_key(bedrock) == "AKIDBEDROCK"
    assert access_key(serving.table.meta.client) == "AKIDTEAM"


def test_without_a_serving_profile_dynamodb_takes_the_default_chain_not_the_bedrock_profile(three_profiles):
    serving = build_serving(settings(bedrock_profile="model-account", serving_backend="dynamodb"))

    # The default chain here is AWS_PROFILE; in ECS it is the task role.
    assert access_key(serving.table.meta.client) == "AKIDGLOBAL"


def test_serving_settings_leave_bedrock_credentials_alone():
    without = settings(agent_llm="bedrock", bedrock_profile="bedrock")
    with_serving = settings(agent_llm="bedrock", bedrock_profile="bedrock", serving_backend="dynamodb",
                            serving_table_name="cuy-loyalty-dev-customer-serving", serving_aws_region="us-east-2")

    assert bedrock_config(with_serving) == bedrock_config(without)
    assert bedrock_config(with_serving).profile == "bedrock"


def test_ecs_style_config_gives_bedrock_its_profile_and_dynamodb_the_default_chain(tmp_path, monkeypatch):
    """As docker-entrypoint.sh writes it in ECS: only [profile bedrock], no default profile."""
    config = tmp_path / "config"
    config.write_text("[profile bedrock]\naws_access_key_id = AKIDBEDROCK\naws_secret_access_key = s\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTASKROLE")  # stands in for the task role's credentials
    ecs = settings(agent_llm="bedrock", bedrock_profile="bedrock", serving_backend="dynamodb")

    assert access_key(bedrock_runtime_client(bedrock_config(ecs))) == "AKIDBEDROCK"
    assert access_key(build_serving(ecs).table.meta.client) == "AKIDTASKROLE"


def test_chat_stores_default_to_memory_without_aws():
    sessions, handoffs = build_chat_stores(settings())

    assert isinstance(sessions, InMemorySessionStore)
    assert isinstance(handoffs, InMemoryHandoffStore)


def test_dynamodb_chat_settings_reach_both_stores():
    configured = settings(conversations_backend="dynamodb", conversations_table_name="conv-1",
                          conversations_aws_region="us-west-2", session_idle_minutes=10,
                          session_retention_days=7, handoff_retention_days=30)

    sessions, handoffs = build_chat_stores(configured)

    assert isinstance(sessions, DynamoSessionStore) and isinstance(handoffs, DynamoHandoffStore)
    assert sessions.table is handoffs.table
    assert (sessions.table.name, sessions.table.meta.client.meta.region_name) == ("conv-1", "us-west-2")
    assert (sessions.idle.total_seconds(), sessions.retention.days, handoffs.retention.days) == (600, 7, 30)


def test_conversations_use_their_own_profile_never_the_bedrock_one(three_profiles):
    both = settings(agent_llm="bedrock", bedrock_profile="model-account",
                    conversations_backend="dynamodb", conversations_aws_profile="team-account")

    sessions, _ = build_chat_stores(both)

    assert access_key(sessions.table.meta.client) == "AKIDTEAM"


def test_in_ecs_conversations_take_the_task_role(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("[profile bedrock]\naws_access_key_id = AKIDBEDROCK\naws_secret_access_key = s\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDTASKROLE")

    sessions, _ = build_chat_stores(settings(agent_llm="bedrock", bedrock_profile="bedrock",
                                             conversations_backend="dynamodb"))

    assert access_key(sessions.table.meta.client) == "AKIDTASKROLE"
