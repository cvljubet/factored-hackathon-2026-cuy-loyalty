"""Domain logic of the inquiry tools, independent of Pydantic AI.

Each function takes AgentDeps and reads the customer from deps.customer_id; none
takes a customer identifier. agents.inquiry_agent registers them with the model.
Unfinished tools report "unavailable" instead of inventing data.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.deps import AgentDeps
from agents.engines.recommendation import payload_for

logger = logging.getLogger(__name__)

ToolStatus = Literal["ok", "unavailable", "error"]

# Every tool the model can call; get_my_profile and recommend_products are implemented.
TOOL_NAMES = (
    "get_my_profile",
    "get_my_products",
    "get_my_agent",
    "get_my_spending",
    "get_my_campaigns",
    "get_my_transactions",
    "get_my_contacts",
    "get_my_complaints",
    "get_branch_info",
    "get_exchange_rate",
    "recommend_products",
)


class ToolResult(BaseModel):
    """What a tool returns to the model."""

    model_config = ConfigDict(frozen=True)

    tool: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    reason: str | None = None


def get_my_profile(deps: AgentDeps) -> ToolResult:
    try:
        profile = deps.profiles.get_profile(deps.customer_id)
    except Exception:
        logger.exception("Profile lookup failed")
        return ToolResult(tool="get_my_profile", status="error", reason="tool_failed")
    if profile is None:
        return ToolResult(tool="get_my_profile", status="unavailable", reason="profile_not_found")
    # The model does not need the identifier to answer; keep it out of the conversation.
    data = {key: value for key, value in profile.items() if key != "customer_id" and value is not None}
    return ToolResult(tool="get_my_profile", status="ok", data=data)


def recommend_products(deps: AgentDeps) -> ToolResult:
    try:
        payload = payload_for(deps.recommendations, deps.context)
    except Exception:
        logger.exception("Recommendation lookup failed")
        return ToolResult(tool="recommend_products", status="error", reason="tool_failed")
    if payload is None:
        return ToolResult(tool="recommend_products", status="unavailable", reason="model_not_ready")
    return ToolResult(
        tool="recommend_products", status="ok", data=payload.model_dump(mode="json", exclude={"customer_id"})
    )


def not_ready(tool: str) -> ToolResult:
    """For tools whose data source (DynamoDB serving tables) is not built yet."""
    return ToolResult(tool=tool, status="unavailable", reason="data_source_not_ready")
