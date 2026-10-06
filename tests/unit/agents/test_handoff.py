import pytest

from agents.engines.escalation import InMemoryHandoffStore
from agents.factory import build_orchestrator
from agents.messages import message
from agents.safety import REDACTED, redact
from agents.sessions import MAX_TURNS, InMemorySessionStore
from agent_testkit import (
    CUSTOMER_ID,
    OTHER_CUSTOMER_ID,
    RecordingServing,
    ScriptedModel,
    answer,
    call_tool,
    model_down,
)

SESSION = "session-1"
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"


class Harness:
    def __init__(self, steps=()):
        self.model = ScriptedModel(steps)
        self.handoffs = InMemoryHandoffStore()
        self.orchestrator = build_orchestrator(
            serving=RecordingServing(),
            model=self.model.model,
            handoffs=self.handoffs,
            sessions=InMemorySessionStore(),
        )

    def say(self, text, customer_id=CUSTOMER_ID, session_id=SESSION, language=None):
        return self.orchestrator.handle(
            customer_id=customer_id, session_id=session_id, user_message=text, language_hint=language
        )

    @property
    def only_handoff(self):
        [handoff] = self.handoffs.handoffs
        return handoff


class TestAcknowledgement:
    @pytest.mark.parametrize(
        "text, language, expected",
        [
            (
                "Quiero hablar con un asesor",
                "es",
                "He derivado tu solicitud a un asesor. El contexto de esta conversación será incluido "
                "para que no tengas que repetir la información.",
            ),
            (
                "Quero falar com um atendente",
                "pt",
                "Encaminhei sua solicitação para um atendente. O contexto desta conversa será incluído "
                "para que você não precise repetir as informações.",
            ),
        ],
    )
    def test_generic_acknowledgement_per_language(self, text, language, expected):
        reply = Harness().say(text)

        assert (reply.status, reply.language, reply.reply) == ("escalated", language, expected)

    @pytest.mark.parametrize(
        "messages, steps",
        [
            (["Quiero hablar con un asesor"], []),
            (["¿Me aprueban un préstamo?"], []),
            (["¿Cuál es mi saldo?", "¿Cuál es mi saldo?"], [model_down(), model_down()]),
        ],
        ids=["human_requested", "credit_decision", "repeated_failures"],
    )
    def test_every_trigger_gives_the_same_acknowledgement_and_no_model_reply(self, messages, steps):
        harness = Harness(steps)

        replies = [harness.say(text) for text in messages]

        assert replies[-1].reply == message("handoff_acknowledgement", "es")
        assert len(harness.model.requests) == len(steps)


class TestHandoffRecord:
    def test_is_created_pending_for_the_authenticated_customer_and_session(self):
        harness = Harness()

        reply = harness.say("Quiero hablar con un asesor", customer_id=OTHER_CUSTOMER_ID, session_id="s-42")

        handoff = harness.only_handoff
        assert handoff.id == reply.handoff_id
        assert (handoff.customer_id, handoff.session_id) == (OTHER_CUSTOMER_ID, "s-42")
        assert (handoff.status, handoff.language) == ("pending", "es")
        assert handoff.created_at.tzinfo is not None

    @pytest.mark.parametrize(
        "messages, steps, reason",
        [
            (["Quiero hablar con un asesor"], [], "human_requested"),
            (["¿Soy elegible para un crédito?"], [], "credit_decision"),
            (["¿Cuál es mi saldo?", "¿Y mis productos?"], [model_down(), model_down()], "repeated_failures"),
        ],
    )
    def test_stores_the_escalation_reason(self, messages, steps, reason):
        harness = Harness(steps)
        for text in messages:
            harness.say(text)

        assert harness.only_handoff.reason == reason

    def test_keeps_recent_turns_open_question_and_verified_facts(self):
        harness = Harness([call_tool("get_my_profile"), answer("Vives en Cusco, Ana.")])

        harness.say("¿En qué ciudad está registrado mi perfil?")
        harness.say("Quiero hablar con un asesor sobre mi tarjeta")

        handoff = harness.only_handoff
        assert handoff.open_question == "Quiero hablar con un asesor sobre mi tarjeta"
        assert [(turn.role, turn.text) for turn in handoff.recent_turns] == [
            ("customer", "¿En qué ciudad está registrado mi perfil?"),
            ("assistant", "Vives en Cusco, Ana."),
            ("customer", "Quiero hablar con un asesor sobre mi tarjeta"),
        ]
        [fact] = handoff.verified_facts
        assert fact.tool == "get_my_profile"
        assert fact.data == {"first_name": "Ana", "last_name": "Quispe", "city": "Cusco", "state": "Cusco", "country": "PE"}

    def test_repeated_failures_carry_both_failed_questions(self):
        harness = Harness([model_down(), model_down()])

        harness.say("¿Cuál es mi saldo?")
        harness.say("¿Y mis productos?")

        handoff = harness.only_handoff
        assert handoff.open_question == "¿Y mis productos?"
        customer_turns = [turn.text for turn in handoff.recent_turns if turn.role == "customer"]
        assert customer_turns == ["¿Cuál es mi saldo?", "¿Y mis productos?"]

    def test_unavailable_tool_results_are_not_recorded_as_facts(self):
        # The test profiles have no relationship agent, so get_my_agent is unavailable.
        harness = Harness([call_tool("get_my_agent"), answer("Aún no disponible.")])

        harness.say("¿Quién es mi ejecutivo?")
        harness.say("Quiero hablar con un asesor")

        assert harness.only_handoff.verified_facts == ()

    def test_history_is_per_customer_and_session(self):
        harness = Harness()

        harness.say("¿Quién ganó el partido?", customer_id=OTHER_CUSTOMER_ID)
        harness.say("¿Quién ganó el partido?", session_id="other-session")
        harness.say("Quiero hablar con un asesor")

        assert [turn.text for turn in harness.only_handoff.recent_turns] == ["Quiero hablar con un asesor"]

    def test_turns_are_capped(self):
        harness = Harness()
        for i in range(MAX_TURNS):
            harness.say(f"pregunta fuera de tema {i}")

        harness.say("Quiero hablar con un asesor")

        turns = harness.only_handoff.recent_turns
        assert len(turns) == MAX_TURNS
        assert turns[-1].text == "Quiero hablar con un asesor"


class TestNoSecretsStored:
    @pytest.mark.parametrize(
        "text, secret",
        [
            ("Quiero hablar con un asesor, mi contraseña es Secreta123", "Secreta123"),
            ("Quero falar com um atendente, minha senha: abc!987", "abc!987"),
            (f"Quiero hablar con un asesor, mi token es Bearer {JWT}", JWT),
            (f"Quiero hablar con un asesor {JWT}", JWT),
            ("Quiero hablar con un asesor sobre la tarjeta 4111 1111 1111 1111", "4111 1111 1111 1111"),
            ("Quiero hablar con un asesor, mi PIN 4321", "4321"),
            ("Quiero hablar con un asesor, la clave de mi tarjeta es 4321", "4321"),
            ("Quiero hablar con un asesor, mi contraseña es: Secreta123", "Secreta123"),
            ("Quero falar com um atendente, minha senha do app é abc123", "abc123"),
            ("Quiero hablar con un asesor, mi clave dinámica es 654321", "654321"),
            ("Quiero hablar con un asesor, el código que me llegó por SMS es 482913", "482913"),
            ("Quiero hablar con un asesor, el código de seguridad de mi tarjeta es 123", "es 123"),
            ("Quiero hablar con un asesor, mi tarjeta es 4111  1111  1111  1111", "4111  1111  1111  1111"),
        ],
    )
    def test_credentials_tokens_and_full_numbers_are_redacted(self, text, secret):
        harness = Harness()

        harness.say(text)

        stored = harness.only_handoff.model_dump_json()
        assert secret not in stored
        assert REDACTED in harness.only_handoff.open_question

    def test_secret_from_an_earlier_turn_is_redacted_in_the_history(self):
        harness = Harness()

        harness.say("mi clave es Hunter2!")
        harness.say("Quiero hablar con un asesor")

        assert "Hunter2!" not in harness.only_handoff.model_dump_json()

    def test_record_has_no_credential_or_backend_fields(self):
        harness = Harness()

        harness.say("Quiero hablar con un asesor")

        fields = set(harness.only_handoff.model_dump())
        assert fields == {
            "id",
            "customer_id",
            "session_id",
            "reason",
            "language",
            "status",
            "created_at",
            "open_question",
            "opening_message",
            "recent_turns",
            "verified_facts",
        }


@pytest.mark.parametrize(
    "text, expected",
    [
        ("mi contraseña es Secreta123", f"mi contraseña es {REDACTED}"),
        ("senha=abc", f"senha={REDACTED}"),
        ("tarjeta 4111111111111111", f"tarjeta {REDACTED}"),
        ("saldo S/ 1.234.567,89 el 2026-10-04", "saldo S/ 1.234.567,89 el 2026-10-04"),
        ("Hola, ¿cuál es mi saldo?", "Hola, ¿cuál es mi saldo?"),
        # The value, not the word after the keyword.
        ("la clave de mi tarjeta es 4321", f"la clave de mi tarjeta es {REDACTED}"),
        ("mi contraseña es: Secreta123", f"mi contraseña es: {REDACTED}"),
        ("contraseña:Secreta123", f"contraseña:{REDACTED}"),
        ("minha senha do app é abc123", f"minha senha do app é {REDACTED}"),
        ("mi clave dinámica 654321", f"mi clave dinámica {REDACTED}"),
        ("mi contraseña es Secreta 123", f"mi contraseña es {REDACTED} {REDACTED}"),
        # Sentence punctuation after the value ends the clause and is kept.
        ("mi clave es Hunter2! quiero un asesor", f"mi clave es {REDACTED}! quiero un asesor"),
        # Only the keyword's clause.
        ("mi PIN es 4321, quiero hablar con un asesor", f"mi PIN es {REDACTED}, quiero hablar con un asesor"),
        ("¿Cuál es mi clave?", "¿Cuál es mi clave?"),
        ("tarjeta 4111  1111\n1111  1111", f"tarjeta {REDACTED}"),
    ],
)
def test_redact(text, expected):
    assert redact(text) == expected
