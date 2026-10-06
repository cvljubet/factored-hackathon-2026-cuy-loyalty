"""Domain logic of the inquiry tools, independent of Pydantic AI.

Each function takes AgentDeps and reads the customer from deps.customer_id; none
takes a customer identifier. agents.inquiry_agent registers them with the model.
Data comes from deps.serving (agents.serving). Each tool returns only the fields it
needs, and public() removes storage keys and backend-only (bk_*) fields after any
deterministic logic has used them, so neither reaches the model.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from agents.deps import AgentDeps
from agents.engines.recommendation import TOOL_NAME as BENEFIT_TOOL, safe_recommendation
from agents.serving import STORAGE_KEYS
from agents.text import city_key

logger = logging.getLogger(__name__)

ToolStatus = Literal["ok", "unavailable", "error"]

# Every tool the model can call.
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
    "recommend_benefit",
)

BACKEND_PREFIX = "bk_"

PROFILE_FIELDS = ("first_name", "last_name", "city", "state", "country")
AGENT_FIELDS = ("agent_name", "agent_specialty", "agent_languages")
# customer_360 holds a 90-day spending summary ending on as_of_date, nothing longer.
SPENDING_WINDOW_DAYS = 90
SPENDING_TOTALS = ("txn_count_90d", "txn_amount_usd_90d", "foreign_txn_count_90d")
SPENDING_CATEGORIES = ("entertainment", "food", "health", "other", "services", "transport")

# What the agent zone keeps per customer (silver_to_gold.py).
MAX_TRANSACTIONS = 20
DEFAULT_TRANSACTIONS = 10
MAX_CONTACTS = 10
MAX_COMPLAINTS = 10
MAX_CAMPAIGNS = 10
MAX_BRANCHES = 10

# ISO 8583 response codes in the data, as a reason the model can explain; the raw code stays backend-only.
DECLINE_REASONS = {
    "05": "declined_by_issuer",
    "14": "invalid_card_number",
    "51": "insufficient_funds",
    "54": "expired_card",
}


class ToolResult(BaseModel):
    """What a tool returns to the model."""

    model_config = ConfigDict(frozen=True)

    tool: str
    status: ToolStatus
    data: dict[str, Any] | None = None
    reason: str | None = None


def public(value: Any) -> Any:
    """value without storage keys or bk_ fields, nested ones included; numbers as int or float."""
    if isinstance(value, Mapping):
        return {
            key: public(v)
            for key, v in value.items()
            if key not in STORAGE_KEYS and not str(key).startswith(BACKEND_PREFIX)
        }
    if isinstance(value, (list, tuple)):
        return [public(v) for v in value]
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _run(tool: str, build: Callable[[], ToolResult]) -> ToolResult:
    try:
        return build()
    except Exception:
        logger.exception("Tool %s failed", tool)
        return ToolResult(tool=tool, status="error", reason="tool_failed")


def _ok(tool: str, data: dict[str, Any]) -> ToolResult:
    return ToolResult(tool=tool, status="ok", data=public(data))


def _unavailable(tool: str, reason: str) -> ToolResult:
    return ToolResult(tool=tool, status="unavailable", reason=reason)


def _cap(requested: int | None, default: int, maximum: int) -> int:
    return max(1, min(requested or default, maximum))


def _profile(deps: AgentDeps, fields: tuple[str, ...]) -> Mapping[str, Any] | None:
    return deps.serving.get_profile(deps.customer_id, fields)


def get_my_profile(deps: AgentDeps) -> ToolResult:
    def build() -> ToolResult:
        profile = _profile(deps, PROFILE_FIELDS)
        if profile is None:
            return _unavailable("get_my_profile", "profile_not_found")
        return _ok("get_my_profile", {k: profile[k] for k in PROFILE_FIELDS if profile.get(k) is not None})

    return _run("get_my_profile", build)


def get_my_products(deps: AgentDeps) -> ToolResult:
    def build() -> ToolResult:
        profile = _profile(deps, ("products",))
        if profile is None:
            return _unavailable("get_my_products", "profile_not_found")
        products = list(profile.get("products") or [])
        return _ok("get_my_products", {"products": products, "count": len(products)})

    return _run("get_my_products", build)


def get_my_agent(deps: AgentDeps) -> ToolResult:
    def build() -> ToolResult:
        profile = _profile(deps, AGENT_FIELDS)
        if profile is None:
            return _unavailable("get_my_agent", "profile_not_found")
        agent = {k: profile[k] for k in AGENT_FIELDS if profile.get(k) is not None}
        if "agent_name" not in agent:
            return _unavailable("get_my_agent", "no_agent_on_record")
        return _ok("get_my_agent", agent)

    return _run("get_my_agent", build)


def get_my_spending(deps: AgentDeps, months: int | None = None) -> ToolResult:
    def build() -> ToolResult:
        categories = tuple(f"spend_usd_{c}" for c in SPENDING_CATEGORIES)
        profile = _profile(deps, ("as_of_date", *SPENDING_TOTALS, *categories))
        if profile is None:
            return _unavailable("get_my_spending", "profile_not_found")
        if not profile.get("as_of_date"):
            return _unavailable("get_my_spending", "no_spending_data")
        end = date.fromisoformat(str(profile["as_of_date"]))
        data: dict[str, Any] = {
            "window_days": SPENDING_WINDOW_DAYS,
            "period_start": (end - timedelta(days=SPENDING_WINDOW_DAYS)).isoformat(),
            "period_end": end.isoformat(),
            "currency": "USD",
            "approved_transactions": profile.get("txn_count_90d", 0),
            "total_spent": profile.get("txn_amount_usd_90d", 0),
            "foreign_transactions": profile.get("foreign_txn_count_90d", 0),
            "by_category": {c: profile[f"spend_usd_{c}"] for c in SPENDING_CATEGORIES if f"spend_usd_{c}" in profile},
        }
        if months is not None and months * 30 != SPENDING_WINDOW_DAYS:
            data["requested_months"] = months
            data["note"] = f"Only the last {SPENDING_WINDOW_DAYS} days are available, not {months} months."
        return _ok("get_my_spending", data)

    return _run("get_my_spending", build)


def get_my_campaigns(deps: AgentDeps) -> ToolResult:
    def build() -> ToolResult:
        campaigns = deps.serving.get_campaigns(deps.customer_id, MAX_CAMPAIGNS)
        # No current campaign is an answer, not a failure.
        return _ok("get_my_campaigns", {"current_campaigns": campaigns, "count": len(campaigns)})

    return _run("get_my_campaigns", build)


def _with_decline_reason(transaction: Mapping[str, Any]) -> dict[str, Any]:
    reason = DECLINE_REASONS.get(str(transaction.get("bk_response_code")))
    if transaction.get("transaction_status") == "Declined" and reason:
        return {**transaction, "decline_reason": reason}
    return dict(transaction)


def get_my_transactions(deps: AgentDeps, limit: int | None = None) -> ToolResult:
    def build() -> ToolResult:
        count = _cap(limit, DEFAULT_TRANSACTIONS, MAX_TRANSACTIONS)
        transactions = [_with_decline_reason(t) for t in deps.serving.get_transactions(deps.customer_id, count)]
        data: dict[str, Any] = {"transactions": transactions, "count": len(transactions), "order": "newest_first"}
        if limit is not None and limit > MAX_TRANSACTIONS:
            data["note"] = f"Only the {MAX_TRANSACTIONS} most recent transactions are available."
        return _ok("get_my_transactions", data)

    return _run("get_my_transactions", build)


def get_my_contacts(deps: AgentDeps) -> ToolResult:
    def build() -> ToolResult:
        contacts = deps.serving.get_contacts(deps.customer_id, MAX_CONTACTS)
        return _ok("get_my_contacts", {"contacts": contacts, "count": len(contacts), "order": "newest_first"})

    return _run("get_my_contacts", build)


def get_my_complaints(deps: AgentDeps) -> ToolResult:
    def build() -> ToolResult:
        complaints = deps.serving.get_complaints(deps.customer_id, MAX_COMPLAINTS)
        return _ok("get_my_complaints", {"complaints": complaints, "count": len(complaints), "order": "newest_first"})

    return _run("get_my_complaints", build)


def get_branch_info(deps: AgentDeps, city: str | None = None) -> ToolResult:
    def build() -> ToolResult:
        # Without a city, the customer's own city.
        asked = city or (_profile(deps, ("city",)) or {}).get("city")
        if not asked or not city_key(asked):
            return _unavailable("get_branch_info", "city_unknown")
        branches = deps.serving.get_branches(city_key(asked))
        data: dict[str, Any] = {"city": asked, "branches": branches[:MAX_BRANCHES], "total_in_city": len(branches)}
        if not branches:
            data["cities_with_branches"] = sorted({str(b["city"]) for b in deps.serving.get_branches() if b.get("city")})
        return _ok("get_branch_info", data)

    return _run("get_branch_info", build)


def get_exchange_rate(
    deps: AgentDeps, base_currency: str | None = None, quote_currency: str | None = None
) -> ToolResult:
    def build() -> ToolResult:
        note = "The bank buys the source currency at buy_rate and sells it at sell_rate."
        if base_currency and quote_currency:
            rate = deps.serving.get_fx_rate(base_currency, quote_currency)
            rates = [rate] if rate else []
        else:
            rates = [
                r
                for r in deps.serving.get_fx_rates()
                if base_currency in (None, r.get("source_currency")) and quote_currency in (None, r.get("target_currency"))
            ]
        if not rates:
            return _unavailable("get_exchange_rate", "currency_pair_not_available")
        return _ok("get_exchange_rate", {"rates": rates, "note": note})

    return _run("get_exchange_rate", build)


def recommend_benefit(deps: AgentDeps) -> ToolResult:
    """The illustrative loyalty benefit the policy chose for the signed-in customer (agents.loyalty). Only the
    customer-safe payload is returned: never the risk score, tier, reason code, model version or strategy."""
    rec = safe_recommendation(deps.recommendations, deps.context)  # a failing store gives the generic benefit
    return _ok(BENEFIT_TOOL, rec.customer_payload())
