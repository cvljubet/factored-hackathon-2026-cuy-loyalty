"""The loyalty recommendation flow (route "recommendation", and the recommend_benefit tool).

    ENGAGEMENT_RISK (LightGBM)  ->  agents.loyalty policy: strategy + illustrative benefit  ->  presenter

The provider decides deterministically; the LLM never chooses the strategy or the benefit. A presenter only
words the customer-safe payload (title, description, reason, illustrative note). ModelPresenter's reply is
checked deterministically and replaced by the fixed template when it adds figures, money or risk language,
or when the model fails, so a reply can never promise terms the catalogue does not contain.
"""

import json
import logging
import re
from typing import Any, Protocol

from pydantic_ai import Agent, UsageLimits
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import Model

from agents.context import AgentContext, Language
from agents.engines.base import EngineResult
from agents.loyalty import GenericLoyaltyProvider, LoyaltyRecommendation, recommend
from agents.messages import message
from agents.sessions import VerifiedFact
from agents.text import compile_patterns, matches_any, normalize

logger = logging.getLogger(__name__)

TOOL_NAME = "recommend_benefit"


class RecommendationProvider(Protocol):
    def get_recommendation(self, customer_id: str, language: Language) -> LoyaltyRecommendation:
        """The customer's loyalty recommendation; a generic one when no engagement score is available."""
        ...


def payload_for(provider: RecommendationProvider, context: AgentContext) -> LoyaltyRecommendation:
    """The provider's recommendation for the context's customer, refusing one issued for anyone else."""
    rec = provider.get_recommendation(context.customer_id, context.language)
    if rec.customer_id != context.customer_id:
        logger.error("Recommendation provider returned a recommendation for a different customer; discarded")
        raise ValueError("Recommendation customer_id does not match the authenticated customer")
    return rec


def safe_recommendation(provider: RecommendationProvider, context: AgentContext) -> LoyaltyRecommendation:
    """payload_for, but a failing or mismatched provider gives the generic benefit (lookup "error")."""
    try:
        return payload_for(provider, context)
    except Exception:
        logger.exception("Loyalty recommendation failed; serving the generic benefit")
        return recommend(context.customer_id, context.language, None, "error")


# Questions about a specific financial product or investment get the loyalty benefit with a fixed note that
# the assistant does not recommend financial products.
_PRODUCT_ADVICE = compile_patterns([
    r"\b(producto|produto|tarjeta|cartao|cuenta|conta|prestamo|emprestimo|credito|inversion|investimento|investir|"
    r"seguro|hipoteca|renda fixa|fondo|fundo|acciones|acoes)\b",
])


def asks_for_product_advice(text: str) -> bool:
    return matches_any(normalize(text), _PRODUCT_ADVICE)


class BenefitPresenter(Protocol):
    def present(
        self, language: Language, question: str, payload: dict[str, Any], product_question: bool
    ) -> tuple[str, dict]:
        """The customer-facing reply and usage trace (tokens, models_used)."""
        ...


class TemplatePresenter:
    """A fixed, reviewed wording of the payload; the local default and the fallback for ModelPresenter."""

    def present(self, language, question, payload, product_question):
        reply = message("benefit_recommendation", language, **{k: payload[k] for k in
                        ("offer_title", "offer_description", "customer_safe_reason", "illustrative_note")})
        if product_question:
            reply = f"{message('no_product_advice', language)} {reply}"
        return reply, {}


_LANGUAGE_NAMES = {"es": "Spanish", "pt": "Portuguese"}
# Anything the payload does not contain and a reply must not add: digits (amounts, rates, dates, percentages),
# money symbols, and risk / model language.
_DIGIT = re.compile(r"\d")
_MONEY = re.compile(r"[%$€£]|\b(usd|eur|brl|mxn|cop|ars|pen|soles?|pesos?|reais|dolares?)\b", re.IGNORECASE)
_RISK_WORDS = compile_patterns([r"\b(riesgo|risco|probabilidad|probabilidade|puntaje|pontuacao|score|modelo|churn|"
                                r"abandono|inactividad|inatividade|predic|previs)"])

presenter_agent = Agent(output_type=str, name="loyalty_benefit_presenter", retries=1)


def _instructions(language: Language, product_question: bool) -> str:
    product = ("The customer asked about a specific financial product or investment: first say in one sentence that "
               "you cannot recommend specific financial products, then present the benefit.\n" if product_question else "")
    return f"""You are the loyalty assistant of a retail bank. Present ONE loyalty benefit to the signed-in customer.

Rules:
- Reply in {_LANGUAGE_NAMES[language]}, warmly, in 2 or 3 short sentences of plain text (no Markdown, no lists).
- Use only the benefit in the JSON: its offer_title, offer_description and customer_safe_reason.
- Do not add any number, amount, percentage, date, deadline, rate, fee, limit or condition that is not in it.
- Never mention risk, scores, models, probabilities, predictions or why the bank selected the customer beyond
  customer_safe_reason.
- End with the illustrative_note, so it is clear this is a demonstration benefit.
{product}"""


class ModelPresenter:
    """Words the payload with the LLM; any reply that breaks the rules above is replaced by the template."""

    def __init__(self, model: Model, fallback: BenefitPresenter | None = None):
        self.model = model
        self.fallback = fallback or TemplatePresenter()

    def present(self, language, question, payload, product_question):
        prompt = f"Customer question: {question}\n\nBenefit (JSON): {json.dumps(payload, ensure_ascii=False)}"
        try:
            result = presenter_agent.run_sync(
                prompt, model=self.model, instructions=_instructions(language, product_question),
                usage_limits=UsageLimits(request_limit=2),
            )
            reply = result.output.strip()
            responses = [m for m in result.all_messages() if isinstance(m, ModelResponse)]
            trace = {"input_tokens": sum(m.usage.input_tokens for m in responses),
                     "output_tokens": sum(m.usage.output_tokens for m in responses),
                     "models_used": tuple(dict.fromkeys(m.model_name for m in responses if m.model_name)),
                     "model_requests": len(responses)}
        except Exception:
            logger.warning("Benefit presenter model failed; using the template", exc_info=True)
            return self.fallback.present(language, question, payload, product_question)
        if not reply or not complies(reply, payload):
            logger.warning("Benefit presenter reply broke the payload rules; using the template")
            return self.fallback.present(language, question, payload, product_question)[0], trace
        return reply, trace


def complies(reply: str, payload: dict[str, Any]) -> bool:
    """No digits or money the payload lacks, and no risk / model language."""
    source = " ".join(str(v) for v in payload.values())
    if _DIGIT.search(reply) and not _DIGIT.search(source):
        return False
    if _MONEY.search(reply) and not _MONEY.search(source):
        return False
    return not matches_any(normalize(reply), _RISK_WORDS)


class RecommendationEngine:
    def __init__(self, provider: RecommendationProvider | None = None, presenter: BenefitPresenter | None = None):
        self.provider = provider or GenericLoyaltyProvider()
        self.presenter = presenter or TemplatePresenter()

    def handle(self, context: AgentContext, user_message: str) -> EngineResult:
        rec = safe_recommendation(self.provider, context)
        payload = rec.customer_payload()
        product_question = asks_for_product_advice(user_message)
        reply, trace = self.presenter.present(context.language, user_message, payload, product_question)
        return EngineResult(
            reply=reply,
            status="answered",
            facts=(VerifiedFact(tool=TOOL_NAME, data=payload),),  # what the customer was shown, for a handoff
            **trace,
        )
