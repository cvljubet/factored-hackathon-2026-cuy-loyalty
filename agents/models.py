"""Which model the agents run on: Bedrock Converse when configured, else the local stand-in.

Bedrock goes through Pydantic AI's BedrockConverseModel (never the direct Anthropic
API). Credentials come from the standard AWS chain (environment, profile or the
task role), never from source code. Building a client or a model makes no request;
the first AWS call happens only when an agent runs.
"""

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.config import Config
from pydantic_ai.models import Model
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.bedrock import BedrockProvider

# Cross-region inference profile IDs.
HAIKU_4_5 = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
# Sonnet 4.6 is the newest Sonnet our model account may invoke (Sonnet 5 and 5.5 are not available to it).
SONNET_4_6 = "us.anthropic.claude-sonnet-4-6"


@dataclass(frozen=True)
class BedrockConfig:
    region: str
    # Haiku serves both routing and inquiry; Sonnet is an optional inquiry fallback.
    inquiry_model_id: str = HAIKU_4_5
    inquiry_fallback_model_id: str | None = None
    router_model_id: str = HAIKU_4_5
    max_tokens: int = 1024
    # Named AWS profile for Bedrock only; None uses the default credential chain.
    profile: str | None = None
    # Botocore defaults (300 s read timeout via Pydantic AI, legacy retries) would let one slow
    # call outlast the HTTP timeouts in front of the API and delay the fallback model.
    read_timeout_seconds: float = 20.0
    max_attempts: int = 2  # including the first try


def bedrock_runtime_client(config: BedrockConfig) -> Any:
    """The bedrock-runtime client the models and the guardrail share (boto3 clients are thread-safe)."""
    session = boto3.Session(profile_name=config.profile, region_name=config.region)
    client_config = Config(
        connect_timeout=5,
        read_timeout=config.read_timeout_seconds,
        # total_max_attempts counts the first try (botocore's max_attempts counts only retries).
        retries={"mode": "standard", "total_max_attempts": config.max_attempts},
    )
    return session.client("bedrock-runtime", config=client_config)


def bedrock_model(model_id: str, config: BedrockConfig, bedrock_client: Any = None) -> BedrockConverseModel:
    """A Converse model; pass bedrock_client to reuse a configured boto3 client (or a stub in tests)."""
    provider = BedrockProvider(bedrock_client=bedrock_client or bedrock_runtime_client(config))
    settings = BedrockModelSettings(temperature=0.0, max_tokens=config.max_tokens)
    return BedrockConverseModel(model_id, provider=provider, settings=settings)


def bedrock_inquiry_model(config: BedrockConfig, bedrock_client: Any = None) -> Model:
    """Haiku for inquiries, falling back to the fallback model (e.g. Sonnet) on API errors when configured.

    FallbackModel moves on when Bedrock raises (throttling, access denied, timeouts),
    so the timeouts above also bound how long the primary model can hold a turn.
    """
    primary = bedrock_model(config.inquiry_model_id, config, bedrock_client)
    if config.inquiry_fallback_model_id is None:
        return primary
    return FallbackModel(primary, bedrock_model(config.inquiry_fallback_model_id, config, bedrock_client))


def bedrock_router_model(config: BedrockConfig, bedrock_client: Any = None) -> BedrockConverseModel:
    # RouteResult comes back as a forced tool call, so the router model must support forced tool
    # choice in Pydantic AI's Bedrock profile (Haiku 4.5 does).
    return bedrock_model(config.router_model_id, config, bedrock_client)
