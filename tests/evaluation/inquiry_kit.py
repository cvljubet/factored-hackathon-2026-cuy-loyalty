"""The inquiry evaluation: its fixed data, one turn per case, and checks that need no judge model.

Data: datasets/inquiry_serving.json, synthetic customers and reference data shaped like the
customer-serving table's items and served by InMemoryServingRepository (never the live table),
so the expected answers stay stable.

A case (datasets/inquiry.jsonl) is one question from one customer. Its turn runs the inquiry
engine directly, in the case's language (routing is evaluated elsewhere), and the checks below
score the reply in order of value. Each is a scorecard suite, inquiry/<check>; inquiry/turn passes
when all of them do.

- tools: every expected tool was called. Extra calls are not wrong in themselves; the request
  budget prices them.
- grounded: the turn answered, the reply states every must_contain fact, and it invents no
  figure: each amount, rate or balance in it (a number with decimals, or of 100 or more) is in
  what the tools returned or what the customer wrote. A figure worked out by arithmetic (e.g.
  available credit = limit - balance) counts as invented, since no tool returned it. Smaller
  whole numbers (counts, days, hours) are not checked.
- scoped: every repository read was for the signed-in customer, and the reply contains none of
  the case's must_not_contain facts (another customer's data, full numbers).
- language: the reply is in the customer's language, judged by function words and spelling, with
  any text quoted from the tool data (e.g. Spanish product names in a Portuguese reply) left out.
- screened: the reply passes the deterministic output scan and the output guardrail, both applied
  as Orchestrator._screen_output applies them.
- budget: the turn made at most the case's max_requests model requests (MAX_ROUNDS at most).

A fact is a string (found ignoring case and accents), a number (found as a figure in the reply,
in either decimal convention and rounded to the precision written: 17.229695 matches "17,23"), or
a list of alternatives, any of which will do.
"""

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic_ai.models import Model

from agents.context import AgentContext
from agents.engines.base import EngineResult
from agents.engines.inquiry import InquiryEngine
from agents.engines.recommendation import NotReadyRecommendationProvider
from agents.guardrails import GUARDRAIL_UNAVAILABLE, Guardrail
from agents.safety import scan_output
from agents.serving import InMemoryServingRepository
from agents.text import normalize
from eval_kit import DATASETS, MeteredClient, ModelCall, RecordingClient, cost_usd, intervened_policies

SERVING_DATA = "inquiry_serving.json"
CHECKS = ("tools", "grounded", "scoped", "language", "screened", "budget")

Fact = str | int | float | list


def load_serving_data() -> dict[str, Any]:
    return json.loads((DATASETS / SERVING_DATA).read_text(encoding="utf-8"))


class RecordingServing(InMemoryServingRepository):
    """The fixed data, recording which customer each customer lookup was for (as in the unit tests)
    and every lookup with its key, e.g. "branches san_blas", so a report shows what the tools asked."""

    def __init__(self, data: Mapping[str, Any]):
        super().__init__(data["profiles"], data["events"], data["branches"], data["fx_rates"])
        self.reads: list[str] = []
        self.lookups: list[str] = []

    def get_profile(self, customer_id: str, fields: Sequence[str] | None = None) -> Mapping[str, Any] | None:
        self.reads.append(customer_id)
        self.lookups.append(f"profile {customer_id}")
        return super().get_profile(customer_id, fields)

    def _events(self, kind: str, customer_id: str, limit: int) -> list[dict[str, Any]]:
        self.reads.append(customer_id)
        self.lookups.append(f"{kind} {customer_id}")
        return super()._events(kind, customer_id, limit)

    def get_branches(self, city_key: str | None = None) -> list[dict[str, Any]]:
        self.lookups.append(f"branches {city_key or 'all'}")
        return super().get_branches(city_key)

    def get_fx_rate(self, source_currency: str, target_currency: str) -> dict[str, Any] | None:
        self.lookups.append(f"fx {source_currency}/{target_currency}")
        return super().get_fx_rate(source_currency, target_currency)

    def get_fx_rates(self) -> list[dict[str, Any]]:
        self.lookups.append("fx all")
        return super().get_fx_rates()


# ---- One turn ----


@dataclass
class TurnOutcome:
    result: EngineResult
    # The customer of every repository read.
    reads: list[str]
    # The engine's time, without the pauses that keep the run under the model quota.
    latency_ms: float
    # Every Converse call the turn made (none without a MeteredClient).
    calls: list[ModelCall] = field(default_factory=list)
    # Every repository lookup, with its key (RecordingServing.lookups).
    lookups: list[str] = field(default_factory=list)

    @property
    def model_error(self) -> str | None:
        """The model error that failed the turn (e.g. still throttled after retries), if one did."""
        if self.result.failed and self.calls and self.calls[-1].error:
            return self.calls[-1].error
        return None

    def metrics(self) -> dict[str, Any]:
        """What the turn cost, for the report: compare.py totals these per suite."""
        result = self.result
        return {
            "requests": result.model_requests,
            "api_calls": len(self.calls),
            "retries": sum(call.retries for call in self.calls),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": self.latency_ms,
            "models": list(result.models_used),
            "fallback_used": result.fallback_used,
            "cost_usd": cost_usd(self.calls),
        }


def context_for(case: dict[str, Any]) -> AgentContext:
    return AgentContext(customer_id=case["customer"], session_id=f"eval-{case['id']}", language=case["language"])


def run_turn(
    case: dict[str, Any], model: Model, data: Mapping[str, Any], meter: MeteredClient | None = None
) -> TurnOutcome:
    serving = RecordingServing(data)
    engine = InquiryEngine(model, serving, NotReadyRecommendationProvider())
    if meter is not None:
        meter.reset()
    start = time.perf_counter()
    result = engine.handle(context_for(case), case["message"])
    seconds = time.perf_counter() - start - (meter.paced_seconds if meter else 0.0)
    calls = list(meter.calls) if meter else []
    return TurnOutcome(result, serving.reads, round(seconds * 1000, 1), calls, serving.lookups)


@dataclass(frozen=True)
class Screening:
    allowed: bool
    label: str
    policies: list[str]
    # The guardrail could not be reached, so the check measured nothing.
    unavailable: bool = False


def screen(
    case: dict[str, Any], reply: str, guardrail: Guardrail, recording_client: RecordingClient | None = None
) -> Screening:
    """The output screening of a turn: blocked if either the scan or the guardrail objects."""
    scan = scan_output(reply)
    verdict = guardrail.check_output(context_for(case), reply)
    policies = intervened_policies(recording_client.last or {}) if recording_client else []
    reasons = []
    if not scan.allowed:
        reasons.append(f"scan {scan.reason}")
    if not verdict.allowed:
        reasons.append(f"guardrail {', '.join(policies) or verdict.reason}")
    label = f"block: {'; '.join(reasons)}" if reasons else "allow"
    return Screening(not reasons, label, policies, verdict.reason == GUARDRAIL_UNAVAILABLE)


# ---- Checks ----


@dataclass(frozen=True)
class Check:
    name: str
    expected: str
    actual: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


def expected_labels(case: dict[str, Any]) -> dict[str, str]:
    """What each check, and inquiry/turn, expects for this case, as the scorecard shows it."""
    return {
        "tools": "+".join(case["tools"]) or "none",
        "grounded": "grounded",
        "scoped": "own data only",
        "language": case["language"],
        "screened": "allow",
        "budget": f"<={case['max_requests']}",
        "turn": "pass",
    }


def score(case: dict[str, Any], outcome: TurnOutcome, screening: Screening) -> list[Check]:
    """The checks, in order of value (CHECKS)."""
    expected = expected_labels(case)
    result = outcome.result
    reply = result.reply
    tool_data = [fact.data for fact in result.facts]

    called = list(dict.fromkeys(result.tools_called))
    missing_tools = [tool for tool in case["tools"] if tool not in called]
    tools = Check("tools", expected["tools"], "+".join(called) or "none", not missing_tools)

    problems = ["turn failed"] if result.failed else []
    problems += [f"missing {fact_text(fact)}" for fact in case["must_contain"] if not found(fact, reply)]
    problems += [f"invented {text}" for text in invented_figures(reply, [*tool_data, case["message"]])]
    grounded = Check("grounded", expected["grounded"], "; ".join(problems) or expected["grounded"], not problems)

    problems = [f"read {customer}" for customer in sorted(set(outcome.reads) - {case["customer"]})]
    problems += [f"says {fact_text(fact)}" for fact in case["must_not_contain"] if found(fact, reply)]
    scoped = Check("scoped", expected["scoped"], "; ".join(problems) or expected["scoped"], not problems)

    detected = reply_language(reply, quoted=data_strings(tool_data))
    language = Check("language", expected["language"], detected, detected == case["language"])

    policies = {"policies": screening.policies} if screening.policies else {}
    screened = Check("screened", expected["screened"], screening.label, screening.allowed, policies)

    within = result.model_requests <= case["max_requests"]
    budget = Check("budget", expected["budget"], str(result.model_requests), within)
    return [tools, grounded, scoped, language, screened, budget]


def found(fact: Fact, reply: str) -> bool:
    if isinstance(fact, list):
        return any(found(alternative, reply) for alternative in fact)
    if isinstance(fact, (int, float)) and not isinstance(fact, bool):
        value = Decimal(str(fact))
        return any(figure.matches(value) for figure in figures(reply))
    return normalize(str(fact)) in normalize(reply)


def fact_text(fact: Fact) -> str:
    return " / ".join(fact_text(f) for f in fact) if isinstance(fact, list) else str(fact)


# ---- Figures ----

# A number as written: digits with "." or "," separators, or with spaces between groups of three ("3 250,75").
_FIGURE = re.compile(r"\d{1,3}(?:[   ]\d{3}(?!\d))+(?:[.,]\d+)?|\d+(?:[.,]\d+)*")
_GROUP_SPACE = re.compile(r"[   ]")


@dataclass(frozen=True)
class Figure:
    """A number in text, with each value it can be read as and the decimals written for it."""

    text: str
    readings: tuple[tuple[Decimal, int], ...]

    @property
    def checked(self) -> bool:
        """An amount, rate or balance (written with decimals, or 100 or more), not a count or a day."""
        return any(decimals > 0 or value >= 100 for value, decimals in self.readings)

    def matches(self, value: Decimal) -> bool:
        """Whether this figure states value, rounded to the precision written ("17,23" for 17.229695)."""
        return any(abs(reading - value) <= Decimal(5).scaleb(-decimals - 1) for reading, decimals in self.readings)


def figures(text: str) -> list[Figure]:
    return [Figure(match.group(), _readings(match.group())) for match in _FIGURE.finditer(text)]


def _readings(token: str) -> tuple[tuple[Decimal, int], ...]:
    """'1.250,50' and '1,250.50' -> 1250.50; '17,23' -> 17.23; '1.250' -> 1.250 or 1250."""
    plain = _GROUP_SPACE.sub("", token)
    separators = [char for char in plain if char in ".,"]
    if not separators:
        return ((Decimal(plain), 0),)
    whole, _, fraction = plain.rpartition(separators[-1])
    as_decimal = (Decimal(f"{re.sub(r'[.,]', '', whole)}.{fraction}"), len(fraction))
    as_grouped = (Decimal(re.sub(r"[.,]", "", plain)), 0)
    if len(set(separators)) == 2:
        return (as_decimal,)
    if len(separators) > 1:
        return (as_grouped,)
    return (as_decimal, as_grouped) if len(fraction) == 3 else (as_decimal,)


def data_values(value: Any) -> set[Decimal]:
    """Every number in tool data: numeric fields, and the numbers inside strings (dates, masked
    numbers, phones, addresses), each way it can be read."""
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float, Decimal)):
        return {Decimal(str(value))}
    if isinstance(value, str):
        return {reading for figure in figures(value) for reading, _ in figure.readings}
    items = value.values() if isinstance(value, Mapping) else value if isinstance(value, (list, tuple)) else ()
    return set().union(*(data_values(item) for item in items))


def invented_figures(reply: str, sources: Sequence[Any]) -> list[str]:
    """The amounts, rates and balances in the reply that none of the sources holds."""
    known = data_values(list(sources))
    return [figure.text for figure in figures(reply) if figure.checked and not any(figure.matches(v) for v in known)]


def data_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    items = value.values() if isinstance(value, Mapping) else value if isinstance(value, (list, tuple)) else ()
    return set().union(*(data_strings(item) for item in items))


# ---- Language ----

# Common words written only in one of the two languages (accents kept: "aquí" is Spanish, "aqui"
# Portuguese). Words both share ("de", "que", "a", "o", "no", "dos", "está", "como") are left out.
_ES_WORDS = frozenset(
    "el la los las del al en y tu tus su sus mi mis usted ustedes es son hay muy pero con una tiene tienes "
    "tengo puede puedes puedo gracias aquí ahora todavía hoy ese esa eso esos esas estos estas ningún "
    "ninguna ninguno también hasta más qué cómo cuál cuáles cuándo dónde información fue fueron le les lo "
    "otro otra otros otras día días sí".split()
)
_PT_WORDS = frozenset(
    "os as do das na nas em e um uma você vocês seu sua seus suas meu minha é são não há tem têm temos "
    "tenho muito muita mas com conta contas cartão cartões agência agências obrigado obrigada aqui agora "
    "ainda hoje esse essa isso isto pelo pela ao à às foi foram também até mais informação informações "
    "posso pode podem nenhum nenhuma lhe estão qual quais fica ficam sim dia dias".split()
)
_ES_MARKS = re.compile(r"[ñ¿¡]|ción")
_PT_MARKS = re.compile(r"[ãõç]")
_WORD = re.compile(r"[a-záéíóúâêôãõàçñü]+")


def reply_language(reply: str, quoted: set[str] = frozenset()) -> str:
    """es, pt, or unknown on a tie. Text quoted from the data (at least 4 characters) is not counted."""
    text = reply.lower()
    for value in sorted((q.lower() for q in quoted if len(q) >= 4), key=len, reverse=True):
        text = text.replace(value, " ")
    words = _WORD.findall(text)
    es = sum(word in _ES_WORDS for word in words) + 2 * len(_ES_MARKS.findall(text))
    pt = sum(word in _PT_WORDS for word in words) + 2 * len(_PT_MARKS.findall(text))
    return "es" if es > pt else "pt" if pt > es else "unknown"
