"""Deterministic safety rules that do not depend on any model."""

import re
from dataclasses import dataclass

from agents.text import compile_patterns, matches_any, normalize

# Questions about the customer's credit score or income. These are answered with a
# fixed policy message, never by the LLM or tools. Patterns run on normalize()d text.
_SENSITIVE_PATTERNS = compile_patterns(
    [
        r"\b(puntaje|puntuacion|calificacion|score|pontuacao|nota)( de)? (credito|crediticio|crediticia)\b",
        r"\bscore\b",
        r"\b(historial|historico) (crediticio|de credito)\b",
        r"\bingresos\b",
        r"\bingreso mensual\b",
        # "cuenta sueldo" / "conta salario" are payroll account products, not income.
        r"(?<!cuenta )(?<!conta )\b(salario|sueldo)\b",
        # "renda fixa" / "renda variavel" are investment products, not income.
        r"\brenda\b(?! fixa| variavel)",
        r"\brendimentos\b",
        r"\bcuanto gano\b",
        r"\bquanto (eu )?ganho\b",
    ]
)


def is_sensitive_request(message: str) -> bool:
    """Whether the message asks about the customer's credit score or income."""
    return matches_any(normalize(message), _SENSITIVE_PATTERNS)


# The agreed rule: 8 or more digits in a row may be a full card, account or document
# number. A single space, hyphen or dot between digits does not break the run, so
# "4111 1111 1111 1111", "1234-5678" and a CPF like "123.456.789-09" are caught.
_DIGIT_RUN = re.compile(r"\d(?:[ .-]?\d){7,}")
# Exemptions: ISO dates ("2026-10-04"), and amounts written right after a currency
# marker ("S/ 1.234.567,89", "R$ 12.345.678,00"). A bare "12.345.678" stays blocked.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY_BEFORE = re.compile(r"(S/|R\$|US\$|\$|€|\b(?:USD|PEN|BRL|EUR|COP|CLP|MXN))\s?$")


@dataclass(frozen=True)
class OutputScanResult:
    allowed: bool
    reason: str | None = None


def _possible_full_numbers(text: str) -> list[re.Match[str]]:
    return [
        match
        for match in _DIGIT_RUN.finditer(text)
        if not _ISO_DATE.fullmatch(match.group())
        and not _CURRENCY_BEFORE.search(text[max(0, match.start() - 5) : match.start()])
    ]


def scan_output(text: str) -> OutputScanResult:
    """Reject text that may contain a full card, account or document number."""
    if _possible_full_numbers(text):
        return OutputScanResult(allowed=False, reason="possible_full_number")
    return OutputScanResult(allowed=True)


REDACTED = "[REDACTED]"
# A credential keyword followed by its value: "mi contraseña es X", "senha: X", "PIN 1234".
_CREDENTIAL = re.compile(
    r"(?i)\b(contrase[ñn]a|password|senha|clave|pin|cvv|cvc|token|otp)\b(\s*(?:es|é|is|:|=)?\s*)\S+"
)
# JWTs (e.g. a pasted Cognito token) and "Bearer <token>".
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")


def redact(text: str) -> str:
    """Text safe to store for a human agent: credentials, tokens and full numbers removed."""
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _CREDENTIAL.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text)
    for match in reversed(_possible_full_numbers(text)):
        text = text[: match.start()] + REDACTED + text[match.end() :]
    return text
