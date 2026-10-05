from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend settings, read from environment variables or the repo-root .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Cognito user pool and SPA client (terraform -chdir=infrastructure/terraform/envs/dev output).
    # These identify the pool; they are not secrets.
    cognito_region: str
    cognito_user_pool_id: str
    cognito_app_client_id: str
    # Cognito signing keys rotate rarely; unknown key IDs trigger a refetch anyway.
    cognito_jwks_cache_seconds: int = 3600

    # Where customer records are served from; "memory" is synthetic data until DynamoDB exists.
    customer_repository: Literal["memory"] = "memory"

    # Chat agent models. "local" is a deterministic stand-in that needs no AWS access;
    # "bedrock" runs Bedrock Converse via Pydantic AI (not enabled until access is provisioned).
    agent_llm: Literal["local", "bedrock"] = "local"
    agent_router: Literal["rules", "bedrock"] = "rules"
    # Bedrock credentials come from the standard AWS chain (env, profile or task role), never settings.
    bedrock_region: str = "us-east-2"
    bedrock_inquiry_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # Optional inquiry fallback, e.g. us.anthropic.claude-sonnet-5-5.
    bedrock_inquiry_fallback_model_id: str | None = None
    bedrock_router_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    # Browser origins allowed to call the API (the Vite dev server by default).
    cors_allow_origins: list[str] = ["http://localhost:5173"]

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
