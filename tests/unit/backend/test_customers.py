import pytest

from app.config import get_settings
from app.customers.dependencies import get_customer_repository
from app.customers.fake_data import SYNTHETIC_CUSTOMERS
from app.customers.models import CustomerProfile
from app.customers.repository import InMemoryCustomerRepository
from app.customers.service import CustomerNotFoundError, get_customer_profile
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
        profile = get_customer_profile(InMemoryCustomerRepository([OWN_CUSTOMER]), "CUST-0042")

        assert profile == CustomerProfile(**OWN_PROFILE)
        assert "email" not in profile.model_dump()
        assert "mobile_phone" not in profile.model_dump()

    def test_unknown_customer_raises(self):
        with pytest.raises(CustomerNotFoundError):
            get_customer_profile(InMemoryCustomerRepository([]), "CUST-0042")


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
