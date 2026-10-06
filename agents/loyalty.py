"""Loyalty recommendations: ML engagement risk -> deterministic strategy -> illustrative benefit.

    LightGBM (ml/engagement_risk)   predicts engagement risk; published as SK = ENGAGEMENT_RISK
    STRATEGY_RULES (this module)    picks the loyalty strategy, deterministically
    CATALOGUE (this module)         picks a controlled, *illustrative* benefit for that strategy
    the LLM                         only words the customer-safe payload (engines/recommendation.py)

The supplied data has no real offer catalogue, so every benefit here is a demonstration concept (points,
category rewards, partner benefits), marked illustrative. None names a rate, fee, credit limit, amount or
eligibility rule, and the model is never shown the risk score, tier, reason code or model version.
"""

import logging
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.context import Language
from agents.serving import ServingRepository

logger = logging.getLogger(__name__)

Strategy = Literal["onboarding", "win_back", "retention", "engagement", "standard_loyalty", "generic_loyalty"]
# How the engagement-risk lookup went. "found" is a valid score; every other value serves generic_loyalty,
# and "error" (the serving store failed) is kept apart from a customer who simply has no score.
RiskLookup = Literal["found", "missing", "invalid", "error", "not_configured"]

# (scoring_source, risk_tier for the model / reason_code for fallbacks) -> strategy. Anything not listed,
# including a fallback for insufficient_data, gets generic_loyalty.
STRATEGY_RULES: dict[tuple[str, str], Strategy] = {
    ("fallback", "insufficient_history_new_customer"): "onboarding",
    ("fallback", "no_eligible_txn_365d"): "win_back",
    ("model", "high"): "retention",
    ("model", "medium"): "engagement",
    ("model", "low"): "standard_loyalty",
}
DEFAULT_STRATEGY: Strategy = "generic_loyalty"


class EngagementRisk(BaseModel):
    """A published ENGAGEMENT_RISK item, validated before any strategy is chosen."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    scoring_source: Literal["model", "fallback"]
    model_eligible: bool
    risk_tier: Literal["high", "medium", "low", "new_customer", "unknown"]
    risk_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason_code: str | None = None
    model_version: str
    as_of_date: str

    @model_validator(mode="after")
    def _consistent(self) -> "EngagementRisk":
        if self.scoring_source == "model":
            if not self.model_eligible or self.risk_score is None or self.risk_tier not in ("high", "medium", "low"):
                raise ValueError("a model item needs model_eligible, a score and a high/medium/low tier")
        elif self.model_eligible or self.risk_score is not None or not self.reason_code:
            raise ValueError("a fallback item has no score and needs a reason_code")
        return self


def strategy_for(risk: EngagementRisk | None) -> Strategy:
    if risk is None:
        return DEFAULT_STRATEGY
    key = risk.risk_tier if risk.scoring_source == "model" else risk.reason_code
    return STRATEGY_RULES.get((risk.scoring_source, key or ""), DEFAULT_STRATEGY)


class IllustrativeOffer(BaseModel):
    """A demonstration benefit. illustrative is always True: no code path may treat it as a real bank
    entitlement. Texts are per language; {category} is filled from the customer's recent spending."""

    model_config = ConfigDict(frozen=True)

    offer_id: str
    strategy: Strategy
    title: dict[Language, str]
    description: dict[Language, str]
    reason: dict[Language, str]
    illustrative: Literal[True] = True


_RELATIONSHIP_REASON = {
    "es": "Según tu relación y actividad reciente con el banco.",
    "pt": "Com base no seu relacionamento e na sua atividade recente com o banco.",
}
CATALOGUE: dict[Strategy, IllustrativeOffer] = {
    "onboarding": IllustrativeOffer(
        offer_id="demo-welcome-points", strategy="onboarding",
        title={"es": "Bono de bienvenida", "pt": "Bônus de boas-vindas"},
        description={"es": "Puntos de bienvenida al hacer tus primeras compras con tu tarjeta durante tu primer mes.",
                     "pt": "Pontos de boas-vindas ao fazer suas primeiras compras com o cartão no seu primeiro mês."},
        reason={"es": "Para acompañarte en tus primeros meses con el banco.",
                "pt": "Para acompanhar seus primeiros meses com o banco."},
    ),
    "win_back": IllustrativeOffer(
        offer_id="demo-comeback-triple-points", strategy="win_back",
        title={"es": "Bono de regreso", "pt": "Bônus de retorno"},
        description={"es": "Triple de puntos en tus compras{category} durante el próximo mes, al volver a usar tu tarjeta.",
                     "pt": "Pontos em triplo nas suas compras{category} no próximo mês, ao voltar a usar o cartão."},
        reason=_RELATIONSHIP_REASON,
    ),
    "retention": IllustrativeOffer(
        offer_id="demo-loyalty-double-points", strategy="retention",
        title={"es": "Recompensa de fidelidad", "pt": "Recompensa de fidelidade"},
        description={"es": "Doble de puntos en tus compras{category} durante los próximos tres meses, más beneficios "
                           "con comercios aliados.",
                     "pt": "Pontos em dobro nas suas compras{category} nos próximos três meses, mais benefícios com "
                           "parceiros."},
        reason=_RELATIONSHIP_REASON,
    ),
    "engagement": IllustrativeOffer(
        offer_id="demo-extra-points", strategy="engagement",
        title={"es": "Impulso de puntos", "pt": "Impulso de pontos"},
        description={"es": "Puntos extra en tus compras{category} durante el próximo mes.",
                     "pt": "Pontos extras nas suas compras{category} no próximo mês."},
        reason=_RELATIONSHIP_REASON,
    ),
    "standard_loyalty": IllustrativeOffer(
        offer_id="demo-partner-benefits", strategy="standard_loyalty",
        title={"es": "Beneficios de tu programa", "pt": "Benefícios do seu programa"},
        description={"es": "Sigues sumando puntos en todas tus compras, y este mes tienes beneficios con comercios "
                           "aliados en tus compras{category}.",
                     "pt": "Você continua somando pontos em todas as compras, e este mês tem benefícios com "
                           "parceiros nas suas compras{category}."},
        reason=_RELATIONSHIP_REASON,
    ),
    "generic_loyalty": IllustrativeOffer(
        offer_id="demo-program-points", strategy="generic_loyalty",
        title={"es": "Programa de puntos", "pt": "Programa de pontos"},
        description={"es": "Acumula puntos en tus compras y canjéalos por beneficios con comercios aliados.",
                     "pt": "Acumule pontos nas suas compras e troque por benefícios com parceiros."},
        reason={"es": "Es un beneficio disponible en el programa de fidelidad.",
                "pt": "É um benefício disponível no programa de fidelidade."},
    ),
}
assert set(CATALOGUE) == set(Strategy.__args__)  # every strategy has exactly one benefit

ILLUSTRATIVE_NOTE = {
    "es": "Beneficio ilustrativo de demostración; no es una oferta real del banco.",
    "pt": "Benefício ilustrativo de demonstração; não é uma oferta real do banco.",
}

# The customer's top spending category over the last 90 days (customer_360, not a model), as a phrase.
SPEND_CATEGORIES = ("entertainment", "food", "health", "services", "transport")  # "other" is not named
CATEGORY_LABELS: dict[str, dict[Language, str]] = {
    "entertainment": {"es": "entretenimiento", "pt": "entretenimento"},
    "food": {"es": "comida", "pt": "alimentação"},
    "health": {"es": "salud", "pt": "saúde"},
    "services": {"es": "servicios", "pt": "serviços"},
    "transport": {"es": "transporte", "pt": "transporte"},
}


class LoyaltyRecommendation(BaseModel):
    """What the policy decided for one customer. customer_payload() is all the LLM may see."""

    model_config = ConfigDict(frozen=True)

    customer_id: str
    language: Language
    strategy: Strategy
    risk_lookup: RiskLookup
    offer: IllustrativeOffer
    category: str | None = None  # a key of CATEGORY_LABELS, from recent spending

    def customer_payload(self) -> dict[str, Any]:
        """Title, description and a customer-safe reason; no score, tier, reason code, model or strategy name."""
        phrase = f" de {CATEGORY_LABELS[self.category][self.language]}" if self.category else ""
        return {
            "offer_title": self.offer.title[self.language],
            "offer_description": self.offer.description[self.language].format(category=phrase),
            "customer_safe_reason": self.offer.reason[self.language],
            "illustrative": True,
            "illustrative_note": ILLUSTRATIVE_NOTE[self.language],
        }


def recommend(customer_id: str, language: Language, risk_item: Mapping[str, Any] | None, lookup: RiskLookup,
              spending: Mapping[str, Any] | None = None) -> LoyaltyRecommendation:
    """The deterministic policy: a validated risk item -> strategy -> catalogue benefit (+ category phrase)."""
    risk = None
    if lookup == "found" and risk_item is not None:
        try:
            risk = EngagementRisk.model_validate(risk_item)
        except ValidationError:
            lookup = "invalid"
    elif lookup == "found":
        lookup = "missing"
    strategy = strategy_for(risk)
    category = top_category(spending) if strategy not in ("onboarding", "generic_loyalty") else None
    return LoyaltyRecommendation(customer_id=customer_id, language=language, strategy=strategy, risk_lookup=lookup,
                                 offer=CATALOGUE[strategy], category=category)


def top_category(spending: Mapping[str, Any] | None) -> str | None:
    """The named category with the most 90-day spend, if any spend at all."""
    if not spending:
        return None
    totals = {c: float(spending.get(f"spend_usd_{c}") or 0) for c in SPEND_CATEGORIES}
    best = max(totals, key=lambda c: (totals[c], c))
    return best if totals[best] > 0 else None


class LoyaltyRecommendationProvider:
    """RecommendationProvider over the serving repository: the customer's ENGAGEMENT_RISK item and, for
    personalisation, their 90-day spending from PROFILE. A failing store never fails the chat: the
    customer gets the generic benefit and the failure is logged as risk_lookup="error"."""

    def __init__(self, serving: ServingRepository):
        self.serving = serving

    def get_recommendation(self, customer_id: str, language: Language) -> LoyaltyRecommendation:
        try:
            item = self.serving.get_engagement_risk(customer_id)
            lookup: RiskLookup = "found" if item is not None else "missing"
        except Exception:
            logger.warning("Engagement-risk lookup failed; serving the generic loyalty benefit", exc_info=True)
            item, lookup = None, "error"
        spending = None
        try:
            spending = self.serving.get_profile(customer_id, tuple(f"spend_usd_{c}" for c in SPEND_CATEGORIES))
        except Exception:
            logger.warning("Spending lookup failed; the benefit is not personalised", exc_info=True)
        rec = recommend(customer_id, language, item, lookup, spending)
        # Never the customer id or the score; strategy and lookup are what operations need.
        logger.info("Loyalty recommendation: strategy=%s risk_lookup=%s", rec.strategy, rec.risk_lookup)
        return rec


class GenericLoyaltyProvider:
    """Serves the generic benefit to everyone (no engagement-risk data configured, e.g. tests)."""

    def get_recommendation(self, customer_id: str, language: Language) -> LoyaltyRecommendation:
        return recommend(customer_id, language, None, "not_configured")
