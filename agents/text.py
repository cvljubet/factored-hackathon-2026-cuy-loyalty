import re
import unicodedata
from collections.abc import Iterable


def normalize(text: str) -> str:
    """Lowercase, accent-free, single-spaced text for keyword matching ("Cartão" -> "cartao")."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip()


def compile_patterns(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in patterns)


def matches_any(normalized_text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(normalized_text) for pattern in patterns)
