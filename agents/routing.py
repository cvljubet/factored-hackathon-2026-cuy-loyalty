"""Routing: which engine handles a message, and in which language.

The orchestrator depends only on the Router protocol, so the rule-based router
below can be replaced by a Bedrock Claude Haiku router that returns the same
RouteResult. Safety-relevant flags (sensitive_request, credit_decision, human
requests) should stay deterministic even then, e.g. by OR-ing the model's
answer with these rules.
"""

import re
from typing import Literal, Protocol, get_args

from pydantic import BaseModel, ConfigDict, Field

from agents.context import AgentContext, Language
from agents.safety import is_sensitive_request
from agents.text import compile_patterns, matches_any, normalize

Engine = Literal["inquiry", "recommendation", "escalation", "out_of_scope"]
ENGINES: tuple[Engine, ...] = get_args(Engine)


class RouteResult(BaseModel):
    """Where a message goes. Also the structured output of the future model router."""

    model_config = ConfigDict(frozen=True)

    engine: Engine = Field(description="Engine that should handle the message")
    language: Language = Field(description="Language the customer wrote in: es (Spanish) or pt (Portuguese)")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this routing, from 0 to 1")
    sensitive_request: bool = Field(
        default=False, description="The customer asks about their own credit score or income"
    )
    # An individual credit or eligibility decision; always handed to a human.
    credit_decision: bool = Field(
        default=False, description="The customer asks whether they would be approved or are eligible for credit"
    )


class Router(Protocol):
    def route(self, context: AgentContext, message: str) -> RouteResult: ...


# Patterns run on normalize()d text: lowercase, no accents.
_HUMAN_REQUEST = compile_patterns(
    [
        r"\bhuman[oa]s?\b",
        r"\b(persona|pessoa) real\b",
        r"\batendente\b",
        r"\b(hablar|conversar|comunicarme|contactar)\b.*\b(con|a)\b.*"
        r"\b(asesor|asesora|ejecutivo|ejecutiva|persona|alguien|agente|operador|operadora)\b",
        r"\b(falar|conversar)\b.*\bcom\b.*\b(atendente|pessoa|alguem|gerente|agente|operador)\b",
    ]
)

_CREDIT_DECISION = compile_patterns(
    [
        r"\b(me|lo|la) (aprueban|aprueba|aprobarian|aprobaria|aprobaran|daran|darian|otorgan|otorgarian)\b",
        r"\b(aprobacion|aprobado|aprobada|elegible|elegibilidad|califico|calificaria)\b",
        r"\bpuedo (obtener|sacar|pedir|solicitar|acceder a) (un|una|el|la) (credito|prestamo)\b",
        r"\b(aprovad[oa]|aprovacao|aprovam|aprovariam)\b",
        r"\b(elegivel|elegibilidade)\b",
        r"\b(consigo|posso) (obter|pegar|pedir|conseguir|tirar) (um|uma|o|a) (credito|emprestimo|financiamento)\b",
        r"\b(concedem|concederiam|dao|dariam) (um|uma|o|a) (credito|emprestimo)\b",
    ]
)

_RECOMMENDATION = compile_patterns(
    [
        r"\brecomend",
        r"\brecomiend",
        r"\bsugier",
        r"\bsugere",
        r"\bsugir",
        r"\bsugest",
        r"\bme conviene\b",
        r"\bdeberia (contratar|tener|abrir|sacar)\b",
        r"\bvale a pena\b",
        r"\bdevo (contratar|abrir|ter)\b",
        r"\bmejor (producto|tarjeta|cuenta) para mi\b",
        r"\bmelhor (produto|cartao|conta) para mim\b",
    ]
)

_INQUIRY = compile_patterns(
    [
        r"\bperfil\b",
        r"\b(mis|meus) dados\b",
        r"\bmis datos\b",
        r"\bproduct",
        r"\bproduto",
        r"\btarjeta",
        r"\bcarto",
        r"\bcartao",
        r"\bcuenta",
        r"\bcontas?\b",
        r"\bsaldo",
        r"\bejecutiv",
        r"\bgerente\b",
        r"\basesor",
        r"\bgast",
        r"\bconsumo",
        r"\bcampan",
        r"\bpromoc",
        r"\boferta",
        r"\bbeneficio",
        r"\b(puntos|pontos|millas|milhas)\b",
        r"\btransac",
        r"\bmovimiento",
        r"\bmovimentac",
        r"\bcompras?\b",
        r"\bcontacto",
        r"\bcontato",
        r"\bllamad",
        r"\bligac",
        r"\breclamo",
        r"\bqueja",
        r"\breclamac",
        r"\bsucursal",
        r"\bagencia",
        r"\boficina",
        r"\bhorario",
        r"\bcambio\b",
        r"\bcotizacion",
        r"\bcotacao",
        r"\bdolar",
        r"\beuros?\b",
    ]
)

# Language evidence, on lowercased text with accents kept.
_PT_WORDS = frozenset(
    "voce você nao não ola olá obrigado obrigada meu minha meus minhas quero falar qual quais quanto "
    "gastei estou tenho conta contas cartão cartao pessoa atendente um uma com do dos das na nas é são "
    "gostaria posso pode renda salário agência produtos meus dados".split()
)
_ES_WORDS = frozenset(
    "usted hola gracias mi mis quiero hablar cuál cual cuáles cuales cuánto cuanto gasté gaste estoy tengo "
    "cuenta cuentas tarjeta persona asesor un una con del el los las la es son tiene puedo quisiera "
    "ingresos sueldo sucursal productos datos".split()
)
_PT_MARKS = re.compile(r"[ãõç]|ção|ções")
_ES_MARKS = re.compile(r"[ñ¿¡]|ción")
_WORD = re.compile(r"[a-záéíóúâêôãõàçñü]+")


def detect_language(message: str, default: Language) -> Language:
    """Spanish or Portuguese by word and spelling evidence; ties keep the default."""
    lowered = message.lower()
    words = _WORD.findall(lowered)
    pt = sum(word in _PT_WORDS for word in words) + 2 * len(_PT_MARKS.findall(lowered))
    es = sum(word in _ES_WORDS for word in words) + 2 * len(_ES_MARKS.findall(lowered))
    if pt > es:
        return "pt"
    if es > pt:
        return "es"
    return default


def is_human_request(message: str) -> bool:
    return matches_any(normalize(message), _HUMAN_REQUEST)


def is_credit_decision(message: str) -> bool:
    return matches_any(normalize(message), _CREDIT_DECISION)


class RuleBasedRouter:
    """Deterministic keyword router for local development and tests."""

    def route(self, context: AgentContext, message: str) -> RouteResult:
        text = normalize(message)
        language = detect_language(message, context.language)
        sensitive = is_sensitive_request(message)

        def result(engine: Engine, confidence: float, **flags: bool) -> RouteResult:
            return RouteResult(engine=engine, language=language, confidence=confidence, **flags)

        if is_credit_decision(message):
            return result("escalation", 0.95, sensitive_request=sensitive, credit_decision=True)
        if is_human_request(message):
            return result("escalation", 0.95, sensitive_request=sensitive)
        if matches_any(text, _RECOMMENDATION):
            return result("recommendation", 0.85, sensitive_request=sensitive)
        if sensitive or matches_any(text, _INQUIRY):
            return result("inquiry", 0.8, sensitive_request=sensitive)
        return result("out_of_scope", 0.6)
