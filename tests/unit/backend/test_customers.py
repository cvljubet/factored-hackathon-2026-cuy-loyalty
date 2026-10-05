from decimal import Decimal

import pytest

from agents.serving import DynamoServingRepository
from app.config import get_settings
from app.customers.dependencies import RepositoryProfileSource, get_customer_repository, get_serving_repository
from app.customers.fake_data import SYNTHETIC_CUSTOMERS
from app.customers.models import CustomerProfile
from app.customers.repository import InMemoryCustomerRepository
from app.customers.service import CustomerNotFoundError, get_customer_profile
from app.main import app
from conftest import OTHER_CUSTOMER, OWN_CUSTOMER

OWN_PROFILE = {
    "customer_id": "CUST-0042",
    "first_name": "Ana",
    "last_name": "Quispe",
    "city": "Cusco",
    "state": "Cusco",
    "country": "PE",
}


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestInMemoryRepository:
    def test_returns_customer_by_id(self):
        repository = InMemoryCustomerRepository([OWN_CUSTOMER, OTHER_CUSTOMER])

        assert repository.get_customer("CUST-0042") == OWN_CUSTOMER

    def test_returns_none_for_unknown_id(self):
        repository = InMemoryCustomerRepository([OWN_CUSTOMER])

        assert repository.get_customer("CUST-UNKNOWN") is None

    def test_default_repository_serves_synthetic_data(self, monkeypatch):
        monkeypatch.setenv("CUSTOMER_REPOSITORY", "memory")
        get_settings.cache_clear()
        get_customer_repository.cache_clear()
        try:
            repository = get_customer_repository()
            assert isinstance(repository, InMemoryCustomerRepository)
            assert repository.get_customer(SYNTHETIC_CUSTOMERS[0].customer_id) == SYNTHETIC_CUSTOMERS[0]
        finally:
            get_settings.cache_clear()
            get_customer_repository.cache_clear()


class TestProfileService:
    def test_returns_minimal_profile_without_contact_details(self):
        profile = get_customer_profile(RepositoryProfileSource(InMemoryCustomerRepository([OWN_CUSTOMER])), "CUST-0042")

        assert profile == CustomerProfile(**OWN_PROFILE)
        assert "email" not in profile.model_dump()
        assert "mobile_phone" not in profile.model_dump()

    def test_unknown_customer_raises(self):
        with pytest.raises(CustomerNotFoundError):
            get_customer_profile(RepositoryProfileSource(InMemoryCustomerRepository([])), "CUST-0042")


class TestMeProfileEndpoint:
    def test_returns_the_signed_in_customers_profile(self, client, token_factory, customer_repository):
        response = client.get("/me/profile", headers=auth_header(token_factory()))

        assert response.status_code == 200
        assert response.json() == OWN_PROFILE
        assert customer_repository.requested_ids == ["CUST-0042"]

    def test_unknown_customer_is_404(self, client, token_factory, customer_repository):
        token = token_factory(**{"custom:customer_id": "CUST-NOT-IN-STORE"})

        response = client.get("/me/profile", headers=auth_header(token))

        assert response.status_code == 404
        assert response.json() == {"detail": "Customer profile not found"}
        assert customer_repository.requested_ids == ["CUST-NOT-IN-STORE"]

    def test_user_without_customer_id_is_403(self, client, token_factory, customer_repository):
        token = token_factory(**{"custom:customer_id": None})

        response = client.get("/me/profile", headers=auth_header(token))

        assert response.status_code == 403
        assert customer_repository.requested_ids == []

    def test_missing_token_is_401(self, client, customer_repository):
        response = client.get("/me/profile")

        assert response.status_code == 401
        assert customer_repository.requested_ids == []

    def test_invalid_token_is_401(self, client, token_factory, customer_repository):
        response = client.get("/me/profile", headers=auth_header(token_factory(aud="some-other-client")))

        assert response.status_code == 401
        assert customer_repository.requested_ids == []


class TestCannotRequestAnotherCustomer:
    """Every way a caller might name another customer is ignored; the token decides."""

    @pytest.mark.parametrize(
        "params, headers",
        [
            ({"customer_id": "CUST-0099"}, {}),
            ({"customerId": "CUST-0099"}, {}),
            ({}, {"X-Customer-Id": "CUST-0099"}),
            ({}, {"customer_id": "CUST-0099"}),
        ],
        ids=["query customer_id", "query customerId", "X-Customer-Id header", "customer_id header"],
    )
    def test_request_input_cannot_override_token_customer(self, client, token_factory, customer_repository, params, headers):
        response = client.get("/me/profile", params=params, headers={**auth_header(token_factory()), **headers})

        assert response.status_code == 200
        assert response.json()["customer_id"] == "CUST-0042"
        assert customer_repository.requested_ids == ["CUST-0042"]

    def test_there_is_no_route_taking_a_customer_id(self, client, token_factory, customer_repository):
        for path in ["/me/profile/CUST-0099", "/customers/CUST-0099", "/customers/CUST-0099/profile"]:
            response = client.get(path, headers=auth_header(token_factory()))
            assert response.status_code == 404, path
        assert customer_repository.requested_ids == []

    def test_post_body_cannot_select_a_customer(self, client, token_factory, customer_repository):
        response = client.post("/me/profile", json={"customer_id": "CUST-0099"}, headers=auth_header(token_factory()))

        assert response.status_code == 405
        assert customer_repository.requested_ids == []

    def test_another_customers_token_claim_only_reaches_their_own_profile(self, client, token_factory):
        token = token_factory(**{"custom:customer_id": "CUST-0099"})

        response = client.get("/me/profile", headers=auth_header(token))

        assert response.json()["customer_id"] == "CUST-0099"
        assert response.json()["first_name"] == OTHER_CUSTOMER.first_name


class ProfileTable:
    """A DynamoDB Table holding PROFILE items as the loader writes them; records each GetItem."""

    def __init__(self, items):
        self.items = {(i["PK"], i["SK"]): i for i in items}
        self.requests = []

    def get_item(self, Key, ProjectionExpression=None, ExpressionAttributeNames=None):
        self.requests.append(Key)
        item = self.items.get((Key["PK"], Key["SK"]))
        if item is None:
            return {}
        wanted = set((ExpressionAttributeNames or {}).values()) or set(item)
        return {"Item": {k: v for k, v in item.items() if k in wanted}}


def stored_profile(customer_id, first_name):
    return {
        "PK": f"CUST#{customer_id}", "SK": "PROFILE", "first_name": first_name, "last_name": "Gutiérrez",
        "city": "Querétaro", "state": "Querétaro", "country": "México", "customer_status": "Active",
        "agent_name": "Luis G.", "bk_segment": "Premium", "customer_id": customer_id,
        "products": [{"product_type": "Tarjeta Crédito", "bk_is_overdue": True, "current_balance": Decimal("1")}],
    }


@pytest.fixture
def dynamo_client(client):
    """The authenticated client with GET /me/profile served from a DynamoDB serving table."""
    table = ProfileTable([stored_profile("CUST-0042", "Enrique"), stored_profile("CUST-0099", "Otro")])
    app.dependency_overrides[get_serving_repository] = lambda: DynamoServingRepository(table)
    return client, table


class TestMeProfileFromServingTable:
    def test_reads_the_tokens_profile_item(self, dynamo_client, token_factory, customer_repository):
        client, table = dynamo_client

        response = client.get("/me/profile", headers=auth_header(token_factory()))

        assert response.status_code == 200
        assert response.json() == {"customer_id": "CUST-0042", "first_name": "Enrique", "last_name": "Gutiérrez",
                                   "city": "Querétaro", "state": "Querétaro", "country": "México"}
        assert table.requests == [{"PK": "CUST#CUST-0042", "SK": "PROFILE"}]
        assert customer_repository.requested_ids == []  # the synthetic customers are not consulted

    def test_exposes_no_storage_or_backend_fields(self, dynamo_client, token_factory):
        client, _ = dynamo_client

        body = client.get("/me/profile", headers=auth_header(token_factory())).text

        for internal in ('"PK"', '"SK"', "CUST#", "bk_", "products", "agent_name", "customer_status", "Premium"):
            assert internal not in body

    @pytest.mark.parametrize(
        "params, headers",
        [({"customer_id": "CUST-0099"}, {}), ({"pk": "CUST#CUST-0099"}, {}), ({}, {"X-Customer-Id": "CUST-0099"})],
    )
    def test_request_input_cannot_select_another_partition(self, dynamo_client, token_factory, params, headers):
        client, table = dynamo_client

        response = client.get("/me/profile", params=params, headers={**auth_header(token_factory()), **headers})

        assert response.json()["first_name"] == "Enrique"
        assert table.requests == [{"PK": "CUST#CUST-0042", "SK": "PROFILE"}]

    def test_a_customer_missing_from_the_table_is_404(self, dynamo_client, token_factory):
        client, _ = dynamo_client

        response = client.get("/me/profile", headers=auth_header(token_factory(**{"custom:customer_id": "CUST-NONE"})))

        assert response.status_code == 404


def test_chat_and_me_profile_share_one_serving_repository(monkeypatch):
    from app.chat import dependencies as chat

    shared = RepositoryProfileSource(InMemoryCustomerRepository([OWN_CUSTOMER]))
    seen = []
    monkeypatch.setattr(chat, "get_serving_repository", lambda: shared)
    monkeypatch.setattr(chat, "build_orchestrator", lambda **kw: seen.append(kw["serving"]))
    chat.get_orchestrator.cache_clear()
    try:
        chat.get_orchestrator()
    finally:
        chat.get_orchestrator.cache_clear()

    assert seen == [shared]
