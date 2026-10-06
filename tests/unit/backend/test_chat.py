import json
import logging

import pytest

from agents.engines.escalation import InMemoryHandoffStore
from agents.factory import build_orchestrator
from agents.local_model import local_model
from agents.sessions import InMemorySessionStore, SessionConflict
from app.chat.dependencies import get_orchestrator
from app.customers.dependencies import RepositoryProfileSource
from app.main import app
from app.observability import TURN_LOGGER


@pytest.fixture
def handoffs():
    return InMemoryHandoffStore()


@pytest.fixture
def chat_client(client, customer_repository, handoffs):
    """The authenticated test client, with the agent served from the test customer repository."""
    orchestrator = build_orchestrator(
        serving=RepositoryProfileSource(customer_repository), model=local_model(), handoffs=handoffs
    )
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator
    return client


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_profile_question_answers_from_the_token_customers_record(chat_client, token_factory, customer_repository):
    response = chat_client.post("/chat", json={"message": "Muéstrame mi perfil"}, headers=auth_header(token_factory()))

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "Estos son los datos de tu perfil: Ana Quispe, Cusco, Cusco, PE."
    assert (body["engine"], body["language"], body["status"], body["escalated"]) == ("inquiry", "es", "answered", False)
    assert len(body["session_id"]) >= 8
    assert customer_repository.requested_ids == ["CUST-0042"]


def test_session_id_is_kept_when_given(chat_client, token_factory):
    response = chat_client.post(
        "/chat", json={"message": "hola", "session_id": "my-session-123"}, headers=auth_header(token_factory())
    )

    assert response.json()["session_id"] == "my-session-123"


@pytest.mark.parametrize("field", ["customer_id", "customerId"])
def test_body_with_a_customer_id_is_rejected(chat_client, token_factory, customer_repository, field):
    response = chat_client.post(
        "/chat", json={"message": "Muéstrame mi perfil", field: "CUST-0099"}, headers=auth_header(token_factory())
    )

    assert response.status_code == 422
    assert customer_repository.requested_ids == []


def test_query_customer_id_is_ignored(chat_client, token_factory, customer_repository):
    response = chat_client.post(
        "/chat",
        params={"customer_id": "CUST-0099"},
        json={"message": "Muéstrame mi perfil"},
        headers=auth_header(token_factory()),
    )

    assert "Ana Quispe" in response.json()["reply"]
    assert customer_repository.requested_ids == ["CUST-0042"]


def test_escalation_is_reported_and_stored(chat_client, token_factory, handoffs):
    response = chat_client.post("/chat", json={"message": "Quero falar com um atendente"}, headers=auth_header(token_factory()))

    body = response.json()
    assert (body["engine"], body["language"], body["escalated"]) == ("escalation", "pt", True)
    [handoff] = handoffs.handoffs
    assert (body["handoff_id"], handoff.customer_id) == (handoff.id, "CUST-0042")
    assert body["reply"].startswith("Encaminhei sua solicitação para um atendente.")


def test_handoff_carries_the_session_context_but_never_the_token(chat_client, token_factory, handoffs):
    token = token_factory()
    first = chat_client.post("/chat", json={"message": "Muéstrame mi perfil"}, headers=auth_header(token))
    session = {"session_id": first.json()["session_id"]}

    chat_client.post("/chat", json={"message": "Quiero hablar con un asesor", **session}, headers=auth_header(token))

    [handoff] = handoffs.handoffs
    assert (handoff.customer_id, handoff.session_id, handoff.reason) == ("CUST-0042", session["session_id"], "human_requested")
    assert [turn.text for turn in handoff.recent_turns][0] == "Muéstrame mi perfil"
    assert handoff.verified_facts[0].data["first_name"] == "Ana"
    stored = handoff.model_dump_json()
    assert token not in stored
    assert token.split(".")[1] not in stored


def test_missing_token_is_401(chat_client, customer_repository):
    response = chat_client.post("/chat", json={"message": "Muéstrame mi perfil"})

    assert response.status_code == 401
    assert customer_repository.requested_ids == []


def test_user_without_customer_id_is_403(chat_client, token_factory, customer_repository):
    token = token_factory(**{"custom:customer_id": None})

    response = chat_client.post("/chat", json={"message": "Muéstrame mi perfil"}, headers=auth_header(token))

    assert response.status_code == 403
    assert customer_repository.requested_ids == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 2001},
        {"message": "hola", "session_id": "bad id!"},
        {"message": "hola", "language": "en"},
    ],
)
def test_invalid_bodies_are_422(chat_client, token_factory, body):
    assert chat_client.post("/chat", json=body, headers=auth_header(token_factory())).status_code == 422


class ConflictingSessions(InMemorySessionStore):
    """Another request always saves the session first."""

    def save(self, customer_id, session_id, state, new_turns):
        raise SessionConflict(session_id)


def test_a_concurrent_save_is_409_and_asks_to_send_again(client, customer_repository, token_factory):
    orchestrator = build_orchestrator(
        serving=RepositoryProfileSource(customer_repository), model=local_model(), sessions=ConflictingSessions()
    )
    app.dependency_overrides[get_orchestrator] = lambda: orchestrator

    response = client.post("/chat", json={"message": "hola", "session_id": "my-session-123"},
                           headers=auth_header(token_factory()))

    assert response.status_code == 409
    assert "send your message again" in response.json()["detail"]


def test_each_turn_is_logged_once_without_the_message(chat_client, token_factory):
    from test_observability import CapturedTurns

    captured = CapturedTurns()
    turns = logging.getLogger(TURN_LOGGER)
    turns.addHandler(captured)
    try:
        response = chat_client.post(
            "/chat",
            json={"message": "Mi tarjeta 4111 1111 1111 1111 y mi clave es hunter2"},
            headers=auth_header(token_factory()),
        )
    finally:
        turns.removeHandler(captured)

    assert response.status_code == 200
    [line] = captured.lines
    assert json.loads(line)["engine"] == response.json()["engine"]
    assert "4111" not in line and "hunter2" not in line
