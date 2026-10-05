"""The LLM-facing inquiry agent: a Pydantic AI Agent with the customer-scoped tools.

Security model: every tool's only trusted input is RunContext[AgentDeps], whose
context.customer_id the backend set from the verified token. No tool declares a
customer parameter (checked at registration below), and Pydantic AI rejects any
argument a tool does not declare, so a model cannot pass or change customer_id.

The model is chosen per run (agents.models): a local FunctionModel, a test model,
or Bedrock Converse once enabled.
"""

import inspect
from collections.abc import Callable
from typing import Annotated

import pydantic_ai
from pydantic import Field
from pydantic_ai import Agent, RunContext

from agents import tools
from agents.deps import AgentDeps
from agents.prompts import inquiry_system_prompt
from agents.tools import ToolResult

# The banner advertises hosted observability on the first run; keep server and test logs clean.
pydantic_ai.BANNER_ENABLED = False


class RoundLimitReached(Exception):
    """The model asked for tools in its last allowed round; the turn fails instead of running them."""


inquiry_agent = Agent(
    deps_type=AgentDeps,
    output_type=str,
    name="loyalty_inquiry",
    # One retry for malformed or refused tool calls (e.g. an injected customer_id).
    retries=1,
)


@inquiry_agent.instructions
def _instructions(ctx: RunContext[AgentDeps]) -> str:
    return inquiry_system_prompt(ctx.deps.context.language)


def _check_round(ctx: RunContext[AgentDeps]) -> None:
    # Tools requested in the final round could never be used, so do not run them.
    if ctx.usage.requests >= ctx.deps.max_rounds:
        raise RoundLimitReached(f"Model requested tools in round {ctx.usage.requests} of {ctx.deps.max_rounds}")


def _customer_scoped_tool(function: Callable[..., ToolResult]) -> Callable[..., ToolResult]:
    """Register a tool, refusing any that would let the model name a customer."""
    model_arguments = list(inspect.signature(function).parameters)[1:]
    if any("customer" in name.lower() for name in model_arguments):
        raise TypeError(f"Tool {function.__name__} must read the customer from RunContext, not an argument")
    return inquiry_agent.tool(function)


Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code, e.g. USD")]


@_customer_scoped_tool
def get_my_profile(ctx: RunContext[AgentDeps]) -> ToolResult:
    """The signed-in customer's name, city, state and country."""
    _check_round(ctx)
    return tools.get_my_profile(ctx.deps)


@_customer_scoped_tool
def get_my_products(ctx: RunContext[AgentDeps]) -> ToolResult:
    """The signed-in customer's products (accounts, cards, loans) and their status."""
    _check_round(ctx)
    return tools.not_ready("get_my_products")


@_customer_scoped_tool
def get_my_agent(ctx: RunContext[AgentDeps]) -> ToolResult:
    """The signed-in customer's assigned relationship agent."""
    _check_round(ctx)
    return tools.not_ready("get_my_agent")


@_customer_scoped_tool
def get_my_spending(
    ctx: RunContext[AgentDeps], months: Annotated[int, Field(ge=1, le=12)] | None = None
) -> ToolResult:
    """The signed-in customer's spending summary by category.

    Args:
        months: Months to look back.
    """
    _check_round(ctx)
    return tools.not_ready("get_my_spending")


@_customer_scoped_tool
def get_my_campaigns(ctx: RunContext[AgentDeps]) -> ToolResult:
    """Campaigns and promotions the signed-in customer is enrolled in or eligible for."""
    _check_round(ctx)
    return tools.not_ready("get_my_campaigns")


@_customer_scoped_tool
def get_my_transactions(
    ctx: RunContext[AgentDeps], limit: Annotated[int, Field(ge=1, le=50)] | None = None
) -> ToolResult:
    """The signed-in customer's recent transactions.

    Args:
        limit: How many transactions to return.
    """
    _check_round(ctx)
    return tools.not_ready("get_my_transactions")


@_customer_scoped_tool
def get_my_contacts(ctx: RunContext[AgentDeps]) -> ToolResult:
    """The signed-in customer's recent contacts with the bank."""
    _check_round(ctx)
    return tools.not_ready("get_my_contacts")


@_customer_scoped_tool
def get_my_complaints(ctx: RunContext[AgentDeps]) -> ToolResult:
    """The signed-in customer's complaints and their status."""
    _check_round(ctx)
    return tools.not_ready("get_my_complaints")


@_customer_scoped_tool
def get_branch_info(ctx: RunContext[AgentDeps], city: Annotated[str, Field(max_length=80)] | None = None) -> ToolResult:
    """Branch locations and opening hours.

    Args:
        city: City to look for branches in.
    """
    _check_round(ctx)
    return tools.not_ready("get_branch_info")


@_customer_scoped_tool
def get_exchange_rate(
    ctx: RunContext[AgentDeps], base_currency: Currency | None = None, quote_currency: Currency | None = None
) -> ToolResult:
    """Current exchange rate between two currencies.

    Args:
        base_currency: Currency to convert from.
        quote_currency: Currency to convert to.
    """
    _check_round(ctx)
    return tools.not_ready("get_exchange_rate")


@_customer_scoped_tool
def recommend_products(ctx: RunContext[AgentDeps]) -> ToolResult:
    """Products recommended for the signed-in customer by the propensity model."""
    _check_round(ctx)
    return tools.recommend_products(ctx.deps)
