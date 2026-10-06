"""The ML loyalty recommendation flow: policy, catalogue, provider, presenter and the chat. No AWS, no Bedrock."""

import json

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from agents import loyalty, tools
from agents.engines.recommendation import (
    ModelPresenter,
    RecommendationEngine,
    TemplatePresenter,
    asks_for_product_advice,
    complies,
)
from agents.factory import build_orchestrator
from agents.loyalty import CATALOGUE, STRATEGY_RULES, LoyaltyRecommendationProvider, recommend, strategy_for
from agents.serving import DynamoServingRepository, InMemoryServingRepository
from agent_testkit import CUSTOMER_ID, OTHER_CUSTOMER_ID, ScriptedModel, call_tool, make_context, make_deps

DEMO = "CLI-00BPQUST6X8L"


def model_item(tier, score):
    return {"scoring_source": "model", "model_eligible": True, "risk_score": score, "risk_tier": tier,
            "model_version": "engagement-risk-v1", "as_of_date": "2026-06-17"}


def fallback_item(tier, reason):
    return {"scoring_source": "fallback", "model_eligible": False, "risk_tier": tier, "reason_code": reason,
            "model_version": "engagement-risk-v1", "as_of_date": "2026-06-17"}


CASES = {
    "model_high": (model_item("high", 0.7098726823271909), "retention", "Recompensa de fidelidad"),
    "model_medium": (model_item("medium", 0.5348661803757675), "engagement", "Impulso de puntos"),
    "model_low": (model_item("low", 0.15919438496494248), "standard_loyalty", "Beneficios de tu programa"),
    "no_txn_365d": (fallback_item("high", "no_eligible_txn_365d"), "win_back", "Bono de regreso"),
    "new_customer": (fallback_item("new_customer", "insufficient_history_new_customer"), "onboarding", "Bono de bienvenida"),
}
FORBIDDEN_IN_PAYLOAD = ("risk", "score", "tier", "reason_code", "model", "engagement-risk", "0.7", "0.5", "0.1",
                        "high", "medium", "low", "churn", "retention", "win_back", "strategy", "probab")


def serving_for(item, spending=None, cid=CUSTOMER_ID):
    return InMemoryServingRepository(profiles={cid: {"first_name": "Ana", **(spending or {})}},
                                     engagement={} if item is None else {cid: item})


# ---- Deterministic policy ----


def test_strategy_mapping_is_exactly_the_agreed_table():
    assert STRATEGY_RULES == {
        ("fallback", "insufficient_history_new_customer"): "onboarding",
        ("fallback", "no_eligible_txn_365d"): "win_back",
        ("model", "high"): "retention",
        ("model", "medium"): "engagement",
        ("model", "low"): "standard_loyalty",
    }
    assert loyalty.DEFAULT_STRATEGY == "generic_loyalty"


@pytest.mark.parametrize("case", list(CASES))
def test_each_engagement_result_gets_its_strategy_and_benefit(case):
    item, strategy, title = CASES[case]
    rec = LoyaltyRecommendationProvider(serving_for(item)).get_recommendation(CUSTOMER_ID, "es")
    assert (rec.strategy, rec.risk_lookup, rec.offer.title["es"]) == (strategy, "found", title)
    assert rec.offer.illustrative is True and rec.customer_payload()["illustrative"] is True


def test_the_mapping_is_deterministic():
    for item, strategy, _ in CASES.values():
        recs = {recommend(CUSTOMER_ID, "es", item, "found").model_dump_json() for _ in range(20)}
        assert len(recs) == 1 and strategy_for(loyalty.EngagementRisk.model_validate(item)) == strategy


def test_a_missing_risk_item_gives_the_generic_benefit():
    rec = LoyaltyRecommendationProvider(serving_for(None)).get_recommendation(CUSTOMER_ID, "es")
    assert (rec.strategy, rec.risk_lookup) == ("generic_loyalty", "missing")


@pytest.mark.parametrize("broken", [
    {**model_item("high", 0.7), "risk_score": None},                     # model row without a score
    {**model_item("high", 0.7), "risk_score": 1.7},                      # score out of range
    {**model_item("extreme", 0.7)},                                      # unknown tier
    {**fallback_item("high", "no_eligible_txn_365d"), "risk_score": 0.4},  # fallback with a score
    {**fallback_item("high", None)},                                     # fallback without a reason
    {"scoring_source": "model"},                                         # truncated item
    {**model_item("low", 0.1), "scoring_source": "heuristic"},           # unknown source
])
def test_a_malformed_risk_item_gives_the_generic_benefit(broken):
    rec = LoyaltyRecommendationProvider(serving_for(broken)).get_recommendation(CUSTOMER_ID, "es")
    assert (rec.strategy, rec.risk_lookup) == ("generic_loyalty", "invalid")


def test_insufficient_data_fallback_is_generic():
    rec = recommend(CUSTOMER_ID, "es", fallback_item("unknown", "insufficient_data"), "found")
    assert (rec.strategy, rec.risk_lookup) == ("generic_loyalty", "found")


def test_a_serving_error_is_generic_but_distinguishable_from_a_missing_score():
    class Down(InMemoryServingRepository):
        def get_engagement_risk(self, customer_id):
            raise RuntimeError("DynamoDB unavailable")

    rec = LoyaltyRecommendationProvider(Down()).get_recommendation(CUSTOMER_ID, "pt")
    assert (rec.strategy, rec.risk_lookup, rec.language) == ("generic_loyalty", "error", "pt")


# ---- Authenticated-customer isolation ----


class RecordingServing(InMemoryServingRepository):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.requested = []

    def get_engagement_risk(self, customer_id):
        self.requested.append(("risk", customer_id))
        return super().get_engagement_risk(customer_id)

    def get_profile(self, customer_id, fields=None):
        self.requested.append(("profile", customer_id))
        return super().get_profile(customer_id, fields)


def test_only_the_authenticated_customer_is_read():
    serving = RecordingServing(engagement={CUSTOMER_ID: CASES["model_low"][0], OTHER_CUSTOMER_ID: CASES["model_high"][0]})
    engine = RecommendationEngine(LoyaltyRecommendationProvider(serving))

    result = engine.handle(make_context(CUSTOMER_ID), "¿Qué beneficio me recomiendas?")

    assert {cid for _, cid in serving.requested} == {CUSTOMER_ID}
    assert "Beneficios de tu programa" in result.reply  # the customer's own (low) benefit, not the other's


def test_dynamodb_lookup_is_one_getitem_on_the_customers_own_key():
    class Table:
        def __init__(self):
            self.keys = []

        def get_item(self, Key, **kw):
            self.keys.append(Key)
            return {"Item": {"PK": Key["PK"], "SK": Key["SK"], **CASES["model_low"][0]}}

    table = Table()
    item = DynamoServingRepository(table).get_engagement_risk(DEMO)
    assert table.keys == [{"PK": f"CUST#{DEMO}", "SK": "ENGAGEMENT_RISK"}]
    assert "PK" not in item and "SK" not in item and item["risk_tier"] == "low"
    with pytest.raises(ValueError):
        DynamoServingRepository(table).get_engagement_risk(f"{DEMO}#SESSION#x")


def test_the_recommendation_tool_takes_no_customer_argument():
    # The registered tool's only parameter is the RunContext; schema tests in test_tools cover every tool.
    import inspect

    from agents import inquiry_agent
    assert list(inspect.signature(inquiry_agent.recommend_benefit).parameters) == ["ctx"]


# ---- What the model and the customer can see ----


@pytest.mark.parametrize("case", list(CASES))
@pytest.mark.parametrize("language", ["es", "pt"])
def test_the_payload_holds_no_score_tier_reason_or_model(case, language):
    payload = recommend(CUSTOMER_ID, language, CASES[case][0], "found").customer_payload()
    assert set(payload) == {"offer_title", "offer_description", "customer_safe_reason", "illustrative", "illustrative_note"}
    text = json.dumps(payload, ensure_ascii=False).lower()
    for word in FORBIDDEN_IN_PAYLOAD:
        assert word not in text, (case, word)
    assert CUSTOMER_ID.lower() not in text


def test_the_catalogue_is_controlled_illustrative_and_has_no_financial_terms():
    assert set(CATALOGUE) == {"onboarding", "win_back", "retention", "engagement", "standard_loyalty", "generic_loyalty"}
    for strategy, offer in CATALOGUE.items():
        assert offer.illustrative is True and offer.strategy == strategy
        for language in ("es", "pt"):
            text = " ".join([offer.title[language], offer.description[language], offer.reason[language]]).lower()
            assert not any(ch.isdigit() for ch in text), (strategy, language)
            for term in ("%", "$", "tasa", "taxa", "interés", "juros", "comisión", "tarifa", "límite", "limite",
                         "crédito", "préstamo", "empréstimo", "aprob", "elegib"):
                assert term not in text, (strategy, language, term)


def test_personalisation_names_the_customers_top_recent_category():
    spending = {"spend_usd_food": 120.0, "spend_usd_transport": 40.0, "spend_usd_other": 999.0}
    es = recommend(CUSTOMER_ID, "es", CASES["model_high"][0], "found", spending).customer_payload()
    pt = recommend(CUSTOMER_ID, "pt", CASES["model_high"][0], "found", spending).customer_payload()
    assert "compras de comida" in es["offer_description"]
    assert "compras de alimentação" in pt["offer_description"]
    plain = recommend(CUSTOMER_ID, "es", CASES["model_high"][0], "found", {"spend_usd_food": 0}).customer_payload()
    assert "en tus compras durante" in plain["offer_description"]
    onboarding = recommend(CUSTOMER_ID, "es", CASES["new_customer"][0], "found", spending).customer_payload()
    assert "comida" not in onboarding["offer_description"]


# ---- Presenters ----


def presenter_model(reply, seen):
    def respond(messages, info):
        seen.append((messages, info.instructions))
        return ModelResponse(parts=[TextPart(reply)])

    return FunctionModel(respond, model_name="test-presenter")


def demo_payload(language="es"):
    return recommend(DEMO, language, CASES["model_low"][0], "found").customer_payload()


def test_model_presenter_uses_a_compliant_reply_and_shows_the_model_only_the_payload():
    seen = []
    good = "¡Tienes Beneficios de tu programa! Sigues sumando puntos y beneficios con comercios aliados. Es un beneficio ilustrativo."
    reply, trace = ModelPresenter(presenter_model(good, seen)).present("es", "¿Qué me recomiendas?", demo_payload(), False)

    assert reply == good and trace["models_used"] == ("test-presenter",)
    prompt = " ".join(p.content for m in seen[0][0] if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart))
    for leak in ("0.159", "low", "engagement-risk", "reason_code", "risk", DEMO):
        assert leak not in prompt
    assert "Spanish" in seen[0][1]


@pytest.mark.parametrize("bad", [
    "Te damos 5000 puntos y 10% de descuento.",                 # invented figures
    "Un bono de USD 50 al volver.",                              # invented money
    "Como tu riesgo de abandono es alto, te ofrecemos esto.",    # risk language
    "Según nuestro modelo, este beneficio es para ti.",          # model language
    "",
])
def test_model_presenter_falls_back_to_the_template_when_the_reply_breaks_the_rules(bad):
    reply, _ = ModelPresenter(presenter_model(bad, [])).present("es", "¿Qué me recomiendas?", demo_payload(), False)
    assert reply == TemplatePresenter().present("es", "", demo_payload(), False)[0]


def test_model_presenter_falls_back_when_the_model_fails():
    def boom(messages, info):
        raise RuntimeError("Bedrock throttled")

    reply, trace = ModelPresenter(FunctionModel(boom)).present("pt", "?", demo_payload("pt"), False)
    assert reply.startswith("Recomendo este benefício: Benefícios do seu programa.") and trace == {}


def test_product_questions_get_a_no_product_advice_note():
    assert asks_for_product_advice("¿Me conviene una tarjeta de crédito nueva?")
    assert asks_for_product_advice("Vale a pena investir em renda fixa?")
    assert not asks_for_product_advice("¿Qué beneficio me recomiendas?")
    reply, _ = TemplatePresenter().present("es", "", demo_payload(), True)
    assert reply.startswith("No puedo recomendarte productos financieros específicos")


def test_complies_allows_only_what_the_payload_contains():
    payload = demo_payload()
    assert complies("Sigues sumando puntos con comercios aliados.", payload)
    assert not complies("Sumas 3 veces más puntos.", payload)
    assert not complies("Tu puntaje de riesgo es bajo.", payload)


# ---- In the chat ----


def chat(serving, presenter=None, model=None):
    return build_orchestrator(serving=serving, presenter=presenter, model=model)


def test_demo_customer_gets_standard_loyalty_and_never_sees_the_score():
    serving = serving_for(CASES["model_low"][0], {"spend_usd_food": 80.0}, cid=DEMO)
    orch = chat(serving)

    reply = orch.handle(customer_id=DEMO, session_id="s-demo", user_message="¿Qué beneficio me recomiendas?")

    rec = LoyaltyRecommendationProvider(serving).get_recommendation(DEMO, "es")
    assert rec.strategy == "standard_loyalty"
    assert (reply.engine, reply.status) == ("recommendation", "answered")
    assert reply.reply.startswith("Te recomiendo este beneficio: Beneficios de tu programa.")
    assert "comida" in reply.reply and "ilustrativo" in reply.reply
    for leak in ("0.159", "0,159", "low", "bajo", "engagement-risk", "riesgo", "standard_loyalty"):
        assert leak not in reply.reply.lower()
    facts = json.dumps([f.model_dump(mode="json") for f in orch.sessions.load(DEMO, "s-demo").facts])
    assert "0.159" not in facts and "risk" not in facts and "low" not in facts


@pytest.mark.parametrize("case", list(CASES))
def test_every_strategy_answers_in_the_chat_in_both_languages(case):
    item, _, title = CASES[case]
    orch = chat(serving_for(item))
    es = orch.handle(customer_id=CUSTOMER_ID, session_id=f"s-{case}", user_message="¿Qué beneficio me recomiendas?")
    pt = orch.handle(customer_id=CUSTOMER_ID, session_id=f"p-{case}", user_message="Que benefício você recomenda para mim?")
    assert title in es.reply and es.status == "answered" and es.language == "es"
    assert pt.language == "pt" and pt.status == "answered" and "ilustrativo de demonstração" in pt.reply


def test_a_failing_store_does_not_fail_the_chat():
    class Down(InMemoryServingRepository):
        def get_engagement_risk(self, customer_id):
            raise RuntimeError("DynamoDB unavailable")

    reply = chat(Down()).handle(customer_id=CUSTOMER_ID, session_id="s-down", user_message="¿Tienes alguna promoción para mí?")
    assert (reply.engine, reply.status) == ("recommendation", "answered") and "Programa de puntos" in reply.reply


def test_inquiry_and_handoff_flows_are_unchanged():
    orch = chat(serving_for(CASES["model_high"][0]))
    handoff = orch.handle(customer_id=CUSTOMER_ID, session_id="s-h", user_message="Quiero hablar con un asesor")
    inquiry = orch.handle(customer_id=CUSTOMER_ID, session_id="s-i", user_message="Muéstrame mis transacciones")
    assert (handoff.engine, handoff.escalated) == ("escalation", True)
    assert inquiry.engine == "inquiry"


def test_the_inquiry_tool_returns_only_the_customer_safe_payload():
    serving = serving_for(CASES["model_high"][0])
    result = tools.recommend_benefit(make_deps(serving, recommendations=LoyaltyRecommendationProvider(serving)))
    assert result.status == "ok" and result.data["offer_title"] == "Recompensa de fidelidad"
    dumped = result.model_dump_json().lower()
    assert "0.709" not in dumped and "high" not in dumped and "risk" not in dumped

    script = ScriptedModel([call_tool("recommend_benefit"), ModelResponse(parts=[TextPart("ok")])])
    from agents.engines.inquiry import InquiryEngine
    InquiryEngine(script.model, serving, LoyaltyRecommendationProvider(serving)).handle(make_context(), "beneficio")
    returned = [p for p in script.requests[1][0][-1].parts if isinstance(p, ToolReturnPart)]
    assert returned[0].content.data["illustrative"] is True and "risk_score" not in json.dumps(returned[0].content.data)
