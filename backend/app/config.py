from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend settings, read from environment variables or the repo-root .env."""

    # env_ignore_empty: an empty variable (e.g. BEDROCK_INQUIRY_FALLBACK_MODEL_ID= from a task
    # definition) means "not set", so optional features stay off instead of getting "".
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Cognito user pool and SPA client (terraform -chdir=infrastructure/terraform/envs/dev output).
    # These identify the pool; they are not secrets.
    cognito_region: str
    cognito_user_pool_id: str
    cognito_app_client_id: str
    # Cognito signing keys rotate rarely; unknown key IDs trigger a refetch anyway.
    cognito_jwks_cache_seconds: int = 3600

    # Where customer records are served from; "memory" is synthetic data until DynamoDB exists.
    customer_repository: Literal["memory"] = "memory"

    # Where the agent's tools read customer and reference data: "dynamodb" is the customer-serving
    # table; "memory" serves only the synthetic profiles above, with no other data.
    serving_backend: Literal["memory", "dynamodb"] = "memory"
    serving_table_name: str = "cuy-loyalty-dev-customer-serving"
    serving_aws_region: str = "us-east-2"
    # Optional named AWS profile for the serving table only (locally: the team account's profile).
    # Unset: the default chain (the ECS task role). Independent of BEDROCK_PROFILE.
    serving_aws_profile: str | None = None

    # Where chat sessions, turns and handoffs are kept: "dynamodb" is the conversations table;
    # "memory" is process-local and lost on restart (local runs and tests).
    conversations_backend: Literal["memory", "dynamodb"] = "memory"
    conversations_table_name: str = "cuy-loyalty-dev-conversations"
    conversations_aws_region: str = "us-east-2"
    # Same rules as SERVING_AWS_PROFILE: unset means the default chain (the ECS task role).
    conversations_aws_profile: str | None = None
    # A session idle for longer starts a new conversation (DynamoDB only).
    session_idle_minutes: int = Field(default=10, gt=0)
    # TTL: sessions and turns expire this long after their last write; handoffs after creation.
    session_retention_days: int = Field(default=7, gt=0)
    handoff_retention_days: int = Field(default=30, gt=0)

    # Chat agent models. "local" is a deterministic stand-in that needs no AWS access;
    # "bedrock" runs Bedrock Converse via Pydantic AI.
    agent_llm: Literal["local", "bedrock"] = "local"
    agent_router: Literal["rules", "bedrock"] = "rules"
    # Bedrock credentials come from the standard AWS chain (env, profile or task role), never settings.
    bedrock_region: str = "us-east-2"
    # Optional named AWS profile used for Bedrock calls only (e.g. a role in the account that runs
    # the models). Unset: the default chain. Other AWS calls never use it.
    bedrock_profile: str | None = None
    bedrock_inquiry_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # Optional inquiry fallback, e.g. us.anthropic.claude-sonnet-4-6 (a model the account can invoke).
    bedrock_inquiry_fallback_model_id: str | None = None
    bedrock_router_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # Per-request limits, so a slow or throttled call fails over (or fails the turn) well within
    # the load balancer and CloudFront timeouts. Attempts include the first try ("standard" retries).
    bedrock_read_timeout_seconds: float = Field(default=20.0, gt=0)
    bedrock_max_attempts: int = Field(default=2, ge=1)
    # Bedrock Guardrails (ApplyGuardrail on input and output). Off unless both are set.
    bedrock_guardrail_id: str | None = None
    bedrock_guardrail_version: str | None = None

    # Browser origins allowed to call the API (the Vite dev server by default).
    cors_allow_origins: list[str] = ["http://localhost:5173"]

    # Level for the application's own loggers (agents.*, app.*); uvicorn configures its own.
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def _guardrail_needs_id_and_version(self) -> "Settings":
        # Half a guardrail configuration would silently leave it off; refuse to start instead.
        if (self.bedrock_guardrail_id is None) != (self.bedrock_guardrail_version is None):
            raise ValueError("Set both BEDROCK_GUARDRAIL_ID and BEDROCK_GUARDRAIL_VERSION, or neither")
        return self

    @property
    def bedrock_guardrail_enabled(self) -> bool:
        return self.bedrock_guardrail_id is not None and self.bedrock_guardrail_version is not None

    @property
    def cognito_issuer(self) -> str:
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    @property
    def cognito_jwks_url(self) -> str:
        return f"{self.cognito_issuer}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
