import re
import unicodedata
from collections.abc import Iterable


def normalize(text: str) -> str:
    """Lowercase, accent-free, single-spaced text for keyword matching ("Cartão" -> "cartao")."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip()


def city_key(city: str) -> str:
    """The branch sort-key form of a city ("Ciudad de México" -> "ciudad_de_mexico"); the same as
    city_key in data/pipelines/serving/load_customer_serving.py, which wrote the keys."""
    return re.sub(r"[^a-z0-9]+", "_", normalize(city)).strip("_")


def compile_patterns(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern) for pattern in patterns)


def matches_any(normalized_text: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(normalized_text) for pattern in patterns)
