"""Which model the agents run on. Bedrock Converse is configured here but not enabled yet.

Bedrock goes through Pydantic AI's BedrockConverseModel (never the direct Anthropic
API). Credentials come from the standard AWS chain (environment, profile or the
task role), never from source code. Building a model makes no request; the first
AWS call happens only when an agent runs.
"""

from dataclasses import dataclass
from typing import Any

from pydantic_ai.models import Model
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.providers.bedrock import BedrockProvider

# Cross-region inference profile IDs.
HAIKU_4_5 = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET_5_5 = "us.anthropic.claude-sonnet-5-5"


@dataclass(frozen=True)
class BedrockConfig:
    region: str
    # Haiku serves both routing and inquiry; Sonnet is an optional inquiry fallback.
    inquiry_model_id: str = HAIKU_4_5
    inquiry_fallback_model_id: str | None = None
    router_model_id: str = HAIKU_4_5
    max_tokens: int = 1024


def bedrock_model(model_id: str, config: BedrockConfig, bedrock_client: Any = None) -> BedrockConverseModel:
    """A Converse model; pass bedrock_client to reuse a configured boto3 client (or a stub in tests)."""
    provider = (
        BedrockProvider(bedrock_client=bedrock_client)
        if bedrock_client is not None
        else BedrockProvider(region_name=config.region)
    )
    settings = BedrockModelSettings(temperature=0.0, max_tokens=config.max_tokens)
    return BedrockConverseModel(model_id, provider=provider, settings=settings)


def bedrock_inquiry_model(config: BedrockConfig, bedrock_client: Any = None) -> Model:
    """Haiku for inquiries, falling back to the fallback model (e.g. Sonnet) on API errors when configured."""
    primary = bedrock_model(config.inquiry_model_id, config, bedrock_client)
    if config.inquiry_fallback_model_id is None:
        return primary
    return FallbackModel(primary, bedrock_model(config.inquiry_fallback_model_id, config, bedrock_client))


def bedrock_router_model(config: BedrockConfig, bedrock_client: Any = None) -> BedrockConverseModel:
    return bedrock_model(config.router_model_id, config, bedrock_client)
