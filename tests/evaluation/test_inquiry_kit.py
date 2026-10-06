"""The inquiry evaluation's own machinery, offline: its dataset, its checks, the metered Bedrock
client, and whole turns with a scripted model or a stubbed Bedrock (no AWS request)."""

import json
from decimal import Decimal

import boto3
import pytest
from botocore.stub import Stubber
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from agents.engines.base import EngineResult
from agents.engines.inquiry import MAX_ROUNDS
from agents.guardrails import NoOpGuardrail
from agents.models import HAIKU_4_5, SONNET_4_6, BedrockConfig, bedrock_inquiry_model
from agents.tools import TOOL_NAMES
from eval_kit import MeteredClient, ModelCall, cost_usd, load_cases
from inquiry_kit import (
    Check,
    TurnOutcome,
    data_values,
    figures,
    found,
    invented_figures,
    load_serving_data,
    reply_language,
    run_turn,
    score,
    screen,
)

CASES = load_cases("inquiry.jsonl")
DATA = load_serving_data()
BY_ID = {case["id"]: case for case in CASES}


def case_id(case: dict) -> str:
    return case["id"]


class TestDataset:
    def test_ids_are_unique(self):
        assert len(BY_ID) == len(CASES)

    @pytest.mark.parametrize("case", CASES, ids=case_id)
    def test_case_is_well_formed(self, case):
        assert case["customer"] in DATA["profiles"]
        assert case["language"] in ("es", "pt")
        assert set(case["tools"]) <= set(TOOL_NAMES)
        assert 1 <= case["max_requests"] <= MAX_ROUNDS
        assert isinstance(case["must_contain"], list) and isinstance(case["must_not_contain"], list)

    @pytest.mark.parametrize("case", CASES, ids=case_id)
    def test_figures_a_reply_must_state_are_in_the_data(self, case):
        """A typo in a figure would fail the case for the wrong reason. (Alternatives may be
        worded by the tools, e.g. the 90-day window, so only figures on their own are checked.)"""
        known = data_values(DATA)
        figures_to_state = [f for f in case["must_contain"] if isinstance(f, (int, float))]
        assert all(Decimal(str(figure)) in known for figure in figures_to_state)

    @pytest.mark.parametrize("case", CASES, ids=case_id)
    def test_forbidden_facts_are_not_the_customers_own_data(self, case):
        customer = case["customer"]
        own = [DATA["profiles"][customer], *(events.get(customer, []) for events in DATA["events"].values())]
        own_text = json.dumps(own, ensure_ascii=False)
        assert [fact for fact in case["must_not_contain"] if found(fact, own_text)] == []


class TestFigures:
    @pytest.mark.parametrize(
        ("text", "values"),
        [
            ("1.250,50", ["1250.50"]),
            ("1,250.50", ["1250.50"]),
            ("17,23", ["17.23"]),
            ("845.2", ["845.2"]),
            ("1.250", ["1.250", "1250"]),
            ("1.250.000", ["1250000"]),
            ("3 250,75", ["3250.75"]),
            ("7305", ["7305"]),
        ],
    )
    def test_each_reading_of_a_figure(self, text, values):
        [figure] = figures(text)
        assert [str(value) for value, _ in figure.readings] == values

    def test_text_splits_into_figures(self):
        text = "El 2026-06-17 pagaste US$ 1.299,00 con la ****7305, a las 18:00."
        assert [f.text for f in figures(text)] == ["2026", "06", "17", "1.299,00", "7305", "18", "00"]

    def test_a_figure_states_a_value_to_the_precision_written(self):
        rate = Decimal("17.229695")
        assert [figure.matches(rate) for figure in figures("17,23 17,2 17 17,32")] == [True, True, True, False]


class TestGrounding:
    tool_data = {
        "current_balance": 845.2,
        "credit_limit": 5000.0,
        "product_number_masked": "****7305",
        "expires": "08/2027",
    }

    def test_figures_from_the_tool_data_are_grounded(self):
        reply = "Debes US$ 845,20 de un límite de US$ 5.000. Tu tarjeta ****7305 vence en 08/2027."
        assert invented_figures(reply, [self.tool_data]) == []

    def test_a_figure_worked_out_from_the_data_is_invented(self):
        assert invented_figures("Te quedan US$ 4.154,80 disponibles.", [self.tool_data]) == ["4.154,80"]

    def test_counts_and_days_are_not_checked(self):
        assert invented_figures("Tienes 3 tarjetas y 2 cuentas desde hace 15 días.", [self.tool_data]) == []

    def test_the_customers_message_is_a_source(self):
        assert invented_figures("Tus 250 compras", [self.tool_data, "¿Cuáles fueron mis 250 compras?"]) == []


@pytest.mark.parametrize(
    ("fact", "reply", "expected"),
    [
        ("Cusco", "Vives en CUSCO.", True),
        ("crédito", "Tu tarjeta de credito", True),
        (845.2, "Debes 845,20 dólares.", True),
        (845.2, "Debes 854,20 dólares.", False),
        (1520.0, "Bruno debe 1.520.", True),
        (["en proceso", "revis"], "Tu reclamo está siendo revisado.", True),
        ([90, "tres meses"], "Solo tengo los últimos 90 días.", True),
        ([90, "tres meses"], "Gastaste 1.875,40 dólares.", False),
    ],
)
def test_found(fact, reply, expected):
    assert found(fact, reply) is expected


class TestReplyLanguage:
    @pytest.mark.parametrize(
        ("reply", "language"),
        [
            ("Vives en Cusco, Perú.", "es"),
            ("Tu tarjeta de crédito termina en 7305 y está activa.", "es"),
            ("Seu gerente de conta é Rafael Souza.", "pt"),
            ("Você tem uma conta corrente e um cartão de crédito.", "pt"),
            ("OK.", "unknown"),
        ],
    )
    def test_detects_the_reply_language(self, reply, language):
        assert reply_language(reply) == language

    def test_text_quoted_from_the_data_does_not_count(self):
        # "El" belongs to the branch's name, not to the reply's own words.
        reply = "Sim: Sucursal El Poblado, Carrera 43A 7-50, de 08:00 a 16:30."
        assert reply_language(reply) == "unknown"
        assert reply_language(reply, quoted={"Sucursal El Poblado", "Carrera 43A 7-50"}) == "pt"


def calls(*tools: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, {}, tool_call_id=f"call-{i}") for i, tool in enumerate(tools)])


def answer(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def scripted(*steps: ModelResponse) -> FunctionModel:
    queue = list(steps)
    return FunctionModel(lambda messages, info: queue.pop(0), model_name="scripted")


def scored(case: dict, outcome: TurnOutcome) -> dict[str, Check]:
    checks = score(case, outcome, screen(case, outcome.result.reply, NoOpGuardrail()))
    return {check.name: check for check in checks}


def failures(checks: dict[str, Check]) -> dict[str, str]:
    return {name: check.actual for name, check in checks.items() if not check.passed}


class TestScriptedTurns:
    def test_a_grounded_answer_passes_every_check(self):
        case = BY_ID["es-profile-city"]
        outcome = run_turn(case, scripted(calls("get_my_profile"), answer("Tu ciudad registrada es Cusco.")), DATA)

        assert failures(scored(case, outcome)) == {}
        assert (outcome.reads, outcome.lookups) == (["CLI-EVAL-ANA"], ["profile CLI-EVAL-ANA"])

    def test_a_wrong_answer_fails_the_checks_it_breaks(self):
        case = BY_ID["es-card-debt"]
        model = scripted(calls("get_my_products"), answer("Você deve US$ 900,00 no seu cartão."))

        checks = scored(case, run_turn(case, model, DATA))

        assert failures(checks) == {"grounded": "missing 845.2; invented 900,00", "language": "pt"}

    def test_the_wrong_tool_and_an_extra_round_are_caught(self):
        case = BY_ID["es-card-debt"]
        model = scripted(calls("get_my_profile"), calls("get_my_agent"), answer("Debes US$ 845,20 en tu tarjeta."))

        checks = scored(case, run_turn(case, model, DATA))

        # Without the products tool the figure was not retrieved, so it counts as invented too.
        assert failures(checks) == {
            "tools": "get_my_profile+get_my_agent",
            "grounded": "invented 845,20",
            "budget": "3",
        }

    def test_another_customers_data_is_caught(self):
        case = BY_ID["es-other-customer"]
        result = EngineResult(reply="Bruno Almeida tiene US$ 2.310,40.", status="answered")

        checks = scored(case, TurnOutcome(result, reads=["CLI-EVAL-ANA", "CLI-EVAL-BRUNO"], latency_ms=1.0))

        assert checks["scoped"].actual == "read CLI-EVAL-BRUNO; says 2310.4; says Almeida"

    def test_a_full_number_is_screened_out(self):
        case = BY_ID["es-card-full-number"]
        outcome = run_turn(case, scripted(answer("Es la 4111 1111 1111 1111.")), DATA)

        checks = scored(case, outcome)

        assert checks["screened"].actual == "block: scan possible_full_number"
        assert not checks["grounded"].passed

    def test_a_failed_turn_is_not_an_answer(self):
        case = BY_ID["es-card-full-number"]
        model = scripted(*(calls("get_my_products") for _ in range(MAX_ROUNDS)))

        checks = scored(case, run_turn(case, model, DATA))

        assert failures(checks) == {"grounded": "turn failed", "budget": "3"}


class StubPacer:
    def __init__(self):
        self.waits = 0

    def wait(self) -> None:
        self.waits += 1


class TestMeteredClient:
    def test_paces_and_records_each_call(self):
        class Runtime:
            meta = "the client's meta"

            def converse(self, **kwargs):
                usage = {"inputTokens": 1200, "outputTokens": 80, "totalTokens": 1280}
                return {"usage": usage, "ResponseMetadata": {"RetryAttempts": 1}}

        pacer = StubPacer()
        meter = MeteredClient(Runtime(), pacer)

        meter.converse(modelId=HAIKU_4_5, messages=[])

        assert pacer.waits == 1
        assert meter.calls == [ModelCall(HAIKU_4_5, input_tokens=1200, output_tokens=80, retries=1)]
        assert meter.meta == "the client's meta"
        meter.reset()
        assert (meter.calls, meter.paced_seconds) == ([], 0.0)

    def test_cost_prices_each_model(self):
        calls_made = [
            ModelCall(HAIKU_4_5, input_tokens=1_000_000, output_tokens=100_000),
            ModelCall(SONNET_4_6, error="ThrottlingException"),
            ModelCall(SONNET_4_6, input_tokens=10_000, output_tokens=1_000),
        ]
        assert cost_usd(calls_made) == pytest.approx(1.0 + 0.5 + 0.03 + 0.015)
        assert cost_usd([ModelCall("some.other-model", input_tokens=10)]) is None


def converse_response(text: str | None = None, tool: str | None = None, tokens: tuple[int, int] = (100, 10)) -> dict:
    content = [{"toolUse": {"toolUseId": "tool-1", "name": tool, "input": {}}}] if tool else [{"text": text}]
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "tool_use" if tool else "end_turn",
        "usage": {"inputTokens": tokens[0], "outputTokens": tokens[1], "totalTokens": sum(tokens)},
        "metrics": {"latencyMs": 5},
    }


class TestStubbedBedrockTurns:
    """The live suite's path, Pydantic AI's Bedrock model over a MeteredClient, with canned responses."""

    @pytest.fixture
    def stubber(self, monkeypatch):
        # Stubbed responses never leave the process; tests/unit's guard against real calls would stop them.
        monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
        client = boto3.client(
            "bedrock-runtime", region_name="us-east-2", aws_access_key_id="test", aws_secret_access_key="test"
        )
        with Stubber(client) as stubber:
            yield stubber
            stubber.assert_no_pending_responses()

    def run(self, stubber, fallback: str | None = None) -> TurnOutcome:
        meter = MeteredClient(stubber.client, StubPacer())
        model = bedrock_inquiry_model(BedrockConfig(region="us-east-2", inquiry_fallback_model_id=fallback), meter)
        return run_turn(BY_ID["es-profile-city"], model, DATA, meter)

    def test_every_request_is_metered(self, stubber):
        stubber.add_response("converse", converse_response(tool="get_my_profile", tokens=(900, 40)))
        stubber.add_response("converse", converse_response("Tu ciudad registrada es Cusco.", tokens=(1000, 12)))

        outcome = self.run(stubber)

        metrics = outcome.metrics()
        assert outcome.result.reply == "Tu ciudad registrada es Cusco."
        assert {key: metrics[key] for key in ("requests", "api_calls", "input_tokens", "output_tokens")} == {
            "requests": 2,
            "api_calls": 2,
            "input_tokens": 1900,
            "output_tokens": 52,
        }
        assert (metrics["models"], metrics["fallback_used"], metrics["cost_usd"]) == ([HAIKU_4_5], False, 0.00216)

    def test_a_throttled_request_answered_by_the_fallback_is_flagged(self, stubber):
        stubber.add_client_error("converse", service_error_code="ThrottlingException", http_status_code=429)
        stubber.add_response("converse", converse_response("Vives en Cusco."))

        outcome = self.run(stubber, fallback=SONNET_4_6)

        assert outcome.model_error is None
        assert (outcome.result.models_used, outcome.result.fallback_used) == ((SONNET_4_6,), True)
        assert [call.error for call in outcome.calls] == ["ThrottlingException", None]

    def test_a_turn_no_model_answered_is_a_model_error(self, stubber):
        stubber.add_client_error("converse", service_error_code="ThrottlingException", http_status_code=429)

        outcome = self.run(stubber)

        assert outcome.result.failed
        assert outcome.model_error == "ThrottlingException"
