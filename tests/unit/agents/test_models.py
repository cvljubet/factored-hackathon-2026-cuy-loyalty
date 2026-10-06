"""Bedrock model configuration, checked without any AWS request."""

import boto3
import pytest
from botocore.exceptions import ProfileNotFound
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.fallback import FallbackModel

from agents.engines.inquiry import InquiryEngine
from agents.loyalty import GenericLoyaltyProvider
from agents.models import (
    HAIKU_4_5,
    SONNET_4_6,
    BedrockConfig,
    bedrock_inquiry_model,
    bedrock_router_model,
    bedrock_runtime_client,
)
from agent_testkit import RecordingServing, make_context


@pytest.fixture
def offline_client():
    """A boto3 client with dummy credentials; constructing it makes no request."""
    return boto3.client(
        "bedrock-runtime", region_name="us-east-2", aws_access_key_id="test", aws_secret_access_key="test"
    )


def test_inquiry_model_defaults_to_haiku_on_converse(offline_client):
    model = bedrock_inquiry_model(BedrockConfig(region="us-east-2"), offline_client)

    assert isinstance(model, BedrockConverseModel)
    assert model.model_name == HAIKU_4_5 == "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_sonnet_fallback_is_optional(offline_client):
    config = BedrockConfig(region="us-east-2", inquiry_fallback_model_id=SONNET_4_6)

    model = bedrock_inquiry_model(config, offline_client)

    assert isinstance(model, FallbackModel)
    assert [m.model_name for m in model.models] == [HAIKU_4_5, "us.anthropic.claude-sonnet-4-6"]


def test_router_model_is_haiku(offline_client):
    assert bedrock_router_model(BedrockConfig(region="us-east-2"), offline_client).model_name == HAIKU_4_5


def test_bedrock_requests_are_blocked_in_tests(offline_client):
    """ALLOW_MODEL_REQUESTS=False (agent_testkit) stops a real call; the turn fails safely instead."""
    model = bedrock_inquiry_model(BedrockConfig(region="us-east-2"), offline_client)
    engine = InquiryEngine(model, RecordingServing(), GenericLoyaltyProvider())

    result = engine.handle(make_context(), "hola")

    assert result.failed is True
    assert result.model_requests == 0


def test_runtime_client_has_explicit_timeouts_and_few_retries():
    client = bedrock_runtime_client(BedrockConfig(region="us-east-2", read_timeout_seconds=12, max_attempts=3))

    config = client.meta.config
    assert (client.meta.region_name, config.read_timeout, config.connect_timeout) == ("us-east-2", 12, 5)
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}


def test_models_built_without_a_client_get_the_configured_one():
    model = bedrock_router_model(BedrockConfig(region="us-east-2", read_timeout_seconds=7))

    assert model.client.meta.config.read_timeout == 7


def test_profile_applies_to_the_bedrock_client_only(tmp_path, monkeypatch):
    config_file = tmp_path / "config"
    config_file.write_text("[profile model-account]\nregion = us-west-2\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))

    client = bedrock_runtime_client(BedrockConfig(region="us-east-2", profile="model-account"))

    # The profile is honoured, and the explicit region still wins over the profile's.
    assert client.meta.region_name == "us-east-2"
    assert boto3.Session().profile_name == "default"
    with pytest.raises(ProfileNotFound):
        bedrock_runtime_client(BedrockConfig(region="us-east-2", profile="missing"))
