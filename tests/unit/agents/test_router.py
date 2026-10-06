import pytest

from agents.routing import RouteResult, RuleBasedRouter, detect_language
from agent_testkit import make_context

router = RuleBasedRouter()


def route(message: str, language: str = "es") -> RouteResult:
    return router.route(make_context(language=language), message)


@pytest.mark.parametrize(
    "message, engine",
    [
        ("¿Cuál es el saldo de mi tarjeta?", "inquiry"),
        ("¿Qué beneficios tengo este mes?", "inquiry"),
        ("¿Dónde queda la sucursal más cercana?", "inquiry"),
        ("¿Quién es mi ejecutivo?", "inquiry"),
        ("Quais são os meus produtos?", "inquiry"),
        ("Qual é o horário da agência?", "inquiry"),
        ("¿Qué producto me recomiendas?", "recommendation"),
        ("Você pode me recomendar um cartão?", "recommendation"),
        ("Quiero hablar con un asesor", "escalation"),
        ("Necesito un humano", "escalation"),
        ("Quero falar com um atendente", "escalation"),
        ("¿Quién ganó el partido ayer?", "out_of_scope"),
        ("Escribe un poema", "out_of_scope"),
    ],
)
def test_routes_to_engine(message, engine):
    assert route(message).engine == engine


@pytest.mark.parametrize(
    "message, engine, language",
    [
        # Loyalty benefit requests: the ML-powered recommendation flow.
        ("¿Qué beneficio me recomiendas?", "recommendation", "es"),
        ("¿Tienes alguna promoción para mí?", "recommendation", "es"),
        ("¿Qué puedo aprovechar de mi programa de beneficios?", "recommendation", "es"),
        ("¿Qué me recomiendas para sacar más provecho del banco?", "recommendation", "es"),
        ("Que benefício você recomenda para mim?", "recommendation", "pt"),
        ("Tem alguma promoção para mim?", "recommendation", "pt"),
        ("Como posso aproveitar melhor o meu programa de pontos?", "recommendation", "pt"),
        # Neighbours that must keep their engine.
        ("Muéstrame mis transacciones", "inquiry", "es"),
        ("¿Qué beneficios tengo este mes?", "inquiry", "es"),
        ("¿Cuántos puntos acumulé?", "inquiry", "es"),
        ("Oi, quero ver meus pontos", "inquiry", "pt"),
        ("Quiero hablar con un asesor", "escalation", "es"),
        ("Quero falar com um atendente sobre uma promoção", "escalation", "pt"),
        ("¿Me aprobarían un préstamo si uso el beneficio?", "escalation", "es"),
    ],
)
def test_loyalty_benefit_routing(message, engine, language):
    routed = route(message, language="pt" if language == "es" else "es")
    assert (routed.engine, routed.language) == (engine, language)


def test_an_english_benefit_request_reaches_the_recommendation_flow():
    # English is not a supported reply language, so only the engine is checked.
    assert route("What benefit would you recommend for me?").engine == "recommendation"


@pytest.mark.parametrize(
    "message, language",
    [
        ("¿Cuál es el saldo de mi cuenta?", "es"),
        ("Quiero hablar con una persona", "es"),
        ("Qual é o saldo da minha conta?", "pt"),
        ("Não consigo ver meus cartões", "pt"),
    ],
)
def test_detects_language(message, language):
    assert route(message, language="es" if language == "pt" else "pt").language == language


def test_ambiguous_message_keeps_the_session_language():
    assert detect_language("ok", default="pt") == "pt"
    assert detect_language("ok", default="es") == "es"


@pytest.mark.parametrize(
    "message",
    [
        "¿Me aprueban un préstamo?",
        "¿Soy elegible para una tarjeta de crédito?",
        "¿Puedo obtener un crédito hipotecario?",
        "Vou ser aprovado para o empréstimo?",
        "Sou elegível para um cartão?",
    ],
)
def test_credit_decisions_are_flagged_and_escalated(message):
    result = route(message)

    assert result.credit_decision is True
    assert result.engine == "escalation"


@pytest.mark.parametrize(
    "message",
    [
        "¿Cuál es mi puntaje de crédito?",
        "¿Qué ingresos tengo registrados?",
        "¿Cuánto es mi sueldo según el banco?",
        "Qual é o meu score?",
        "Qual renda vocês têm cadastrada?",
    ],
)
def test_sensitive_requests_are_flagged(message):
    result = route(message)

    assert result.sensitive_request is True
    assert result.credit_decision is False


@pytest.mark.parametrize("message", ["¿Cuál es el saldo de mi cuenta sueldo?", "Quero investir em renda fixa"])
def test_product_names_are_not_sensitive(message):
    assert route(message).sensitive_request is False


def test_result_carries_confidence_and_flags():
    result = route("¿Cuál es el saldo de mi tarjeta?")

    assert 0 <= result.confidence <= 1
    assert result.sensitive_request is False
    assert result.credit_decision is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"engine": "chitchat", "language": "es", "confidence": 0.5},
        {"engine": "inquiry", "language": "en", "confidence": 0.5},
        {"engine": "inquiry", "language": "es", "confidence": 1.5},
    ],
)
def test_route_result_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        RouteResult(**kwargs)
