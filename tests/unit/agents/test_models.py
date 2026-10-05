"""Bedrock model configuration, checked without any AWS request."""

import boto3
import pytest
from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.fallback import FallbackModel

from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import NotReadyRecommendationProvider
from agents.models import HAIKU_4_5, SONNET_5_5, BedrockConfig, bedrock_inquiry_model, bedrock_router_model
from agent_testkit import RecordingProfileSource, make_context


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
    config = BedrockConfig(region="us-east-2", inquiry_fallback_model_id=SONNET_5_5)

    model = bedrock_inquiry_model(config, offline_client)

    assert isinstance(model, FallbackModel)
    assert [m.model_name for m in model.models] == [HAIKU_4_5, "us.anthropic.claude-sonnet-5-5"]


def test_router_model_is_haiku(offline_client):
    assert bedrock_router_model(BedrockConfig(region="us-east-2"), offline_client).model_name == HAIKU_4_5


def test_bedrock_requests_are_blocked_in_tests(offline_client):
    """ALLOW_MODEL_REQUESTS=False (agent_testkit) stops a real call; the turn fails safely instead."""
    model = bedrock_inquiry_model(BedrockConfig(region="us-east-2"), offline_client)
    engine = InquiryEngine(model, RecordingProfileSource(), NotReadyRecommendationProvider())

    result = engine.handle(make_context(), "hola")

    assert result.failed is True
    assert result.model_requests == 0
