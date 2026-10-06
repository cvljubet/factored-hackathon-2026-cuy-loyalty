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


def _possible_full_numbers(text: str, digit_run: re.Pattern[str] = _DIGIT_RUN) -> list[re.Match[str]]:
    return [
        match
        for match in digit_run.finditer(text)
        if not _ISO_DATE.fullmatch(match.group())
        and not _CURRENCY_BEFORE.search(text[max(0, match.start() - 5) : match.start()])
    ]


def scan_output(text: str) -> OutputScanResult:
    """Reject text that may contain a full card, account or document number."""
    if _possible_full_numbers(text):
        return OutputScanResult(allowed=False, reason="possible_full_number")
    return OutputScanResult(allowed=True)


REDACTED = "[REDACTED]"
# Words that name a secret. "código" covers "código de seguridad", "el código que me llegó por SMS".
_CREDENTIAL_KEYWORD = re.compile(
    r"(?i)\b(contrase[ñn]a|password|passcode|senha|clave|chave|pin|nip|cvv2?|cvc|token|otp|c[óo]digo)\b"
)
# The clause a keyword's value must be in: up to a comma, semicolon or sentence end, or a line break.
_CLAUSE_END = re.compile(r"[,;.!?](?=\s|$)|\n")
# What introduces the value: "es", "é", "is", "son", ":" or "=" ("la clave de mi tarjeta es 4321").
_VALUE_SEPARATOR = re.compile(r"(?i)(?<!\w)(?:es|é|is|son|era)(?!\w)|[:=]")
_TOKEN = re.compile(r"\S+")
# Full numbers in stored text: like _DIGIT_RUN, but also across line breaks or a few spaces
# ("4111  1111  1111  1111"). Stored text may be over-redacted; replies are scanned with _DIGIT_RUN.
_STORED_DIGIT_RUN = re.compile(r"\d(?:(?:\s{1,3}|[.-])?\d){7,}")
# JWTs (e.g. a pasted Cognito token) and "Bearer <token>".
_JWT = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")


def _credential_spans(text: str) -> list[tuple[int, int]]:
    """Where the values after credential keywords are, within each keyword's clause.

    The first word after a separator is the value ("contraseña es: Secreta123"); any word with a
    digit in the clause is one too ("mi PIN 4321", "clave dinámica 654321"). With neither, the word
    right after the keyword is taken, so a bare "senha abc" is still covered.
    """
    spans = []
    for keyword in _CREDENTIAL_KEYWORD.finditer(text):
        end = _CLAUSE_END.search(text, keyword.end())
        clause_end = end.start() if end else len(text)
        tokens = [_value_part(text, t.start(), t.end()) for t in _TOKEN.finditer(text, keyword.end(), clause_end)]
        # A token that is only a separator ("es", "es:", ":") is never a value.
        values = [(s, e) for s, e in tokens if s < e and not _VALUE_SEPARATOR.fullmatch(text[s:e])]
        separator = _VALUE_SEPARATOR.search(text, keyword.end(), clause_end)
        found = {(s, e) for s, e in values if any(c.isdigit() for c in text[s:e])}
        if separator:
            # The first value that ends after the separator; "senha=abc" is one token, "abc" its value.
            found.update([(max(s, separator.end()), e) for s, e in values if e > separator.end()][:1])
        elif not found and values:
            found.add(values[0])
        spans.extend(found)
    return sorted(spans)


def _value_part(text: str, start: int, end: int) -> tuple[int, int]:
    """A token without a leading separator: "es:Secreta" -> "Secreta", ":abc" -> "abc", ":" -> empty."""
    match = re.match(r"(?i)(?:(?:es|é|is|son|era)?[:=]+)", text[start:end])
    return (start + match.end(), end) if match else (start, end)


def _replace(text: str, spans: list[tuple[int, int]]) -> str:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        text = text[:start] + REDACTED + text[end:]
    return text


def redact(text: str) -> str:
    """Text safe to store for a human agent: credentials, tokens and full numbers removed."""
    text = _JWT.sub(REDACTED, text)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _replace(text, _credential_spans(text))
    numbers = _possible_full_numbers(text, _STORED_DIGIT_RUN)
    return _replace(text, [(match.start(), match.end()) for match in numbers])
