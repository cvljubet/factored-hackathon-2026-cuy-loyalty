from pydantic_ai.models.bedrock import BedrockConverseModel
from pydantic_ai.models.function import FunctionModel

from agents.model_router import HybridRouter
from agents.routing import RuleBasedRouter
from app.chat.dependencies import build_inquiry_model, build_router
from app.config import Settings

BASE = {"cognito_region": "us-east-2", "cognito_user_pool_id": "us-east-2_x", "cognito_app_client_id": "c"}


def test_defaults_need_no_aws_access():
    settings = Settings(**BASE)

    assert isinstance(build_inquiry_model(settings), FunctionModel)
    assert isinstance(build_router(settings), RuleBasedRouter)


def test_bedrock_settings_select_converse_models_without_calling_aws():
    settings = Settings(**BASE, agent_llm="bedrock", agent_router="bedrock")

    model = build_inquiry_model(settings)

    assert isinstance(model, BedrockConverseModel)
    assert model.model_name == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert isinstance(build_router(settings), HybridRouter)
