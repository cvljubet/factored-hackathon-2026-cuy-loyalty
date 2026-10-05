import pytest

from agents.safety import is_sensitive_request, scan_output


@pytest.mark.parametrize(
    "text",
    [
        "Tu tarjeta 4111111111111111 está activa.",
        "Tu tarjeta 4111 1111 1111 1111 está activa.",
        "Cuenta 1234-5678-9012",
        "Tu DNI es 12345678.",
        "Documento: 12 345 678",
        "Documento: 12.345.678",
        "Seu CPF é 123.456.789-09.",
        "Conta 00012345",
        # An amount needs a currency marker to be exempt.
        "Tu saldo es 12.345.678,90",
    ],
)
def test_blocks_possible_full_numbers(text):
    result = scan_output(text)

    assert result.allowed is False
    assert result.reason == "possible_full_number"


@pytest.mark.parametrize(
    "text",
    [
        "Tu tarjeta terminada en 1111 está activa.",
        "Tu saldo es S/ 1.234.567,89.",
        "Seu saldo é R$ 12.345.678,00.",
        "El límite es USD 25.000.000.",
        "Tienes 1234567 puntos.",
        "Tu última compra fue el 2026-10-04.",
        "Abierto de 9:00 a 18:00, de lunes a viernes.",
        "Referencia ****1234",
        "",
    ],
)
def test_allows_text_without_full_numbers(text):
    assert scan_output(text).allowed is True


@pytest.mark.parametrize(
    "message, sensitive",
    [
        ("¿Cuál es mi puntaje crediticio?", True),
        ("Dime mi score de crédito", True),
        ("¿Cuánto gano según ustedes?", True),
        ("Qual é a minha pontuação de crédito?", True),
        ("Quanto eu ganho por mês?", True),
        ("¿Cuáles son mis beneficios?", False),
        ("Quero abrir uma conta salário", False),
    ],
)
def test_sensitive_request_detection(message, sensitive):
    assert is_sensitive_request(message) is sensitive
