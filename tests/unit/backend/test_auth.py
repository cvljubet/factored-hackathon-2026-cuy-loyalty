import time

import pytest

from app.auth import InvalidTokenError
from conftest import ISSUER, OTHER_KEY

ONE_HOUR_AGO = int(time.time()) - 3600

# Each case is a token that must be rejected: (id, make_token kwargs).
INVALID_TOKENS = [
    ("invalid signature", {"key": OTHER_KEY}),
    ("expired", {"iat": ONE_HOUR_AGO - 60, "exp": ONE_HOUR_AGO}),
    ("wrong issuer", {"iss": "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_OtherPool"}),
    ("wrong audience", {"aud": "some-other-client"}),
    ("wrong token_use", {"token_use": "access"}),
    ("missing token_use", {"token_use": None}),
    ("missing exp", {"exp": None}),
    ("unknown kid", {"kid": "not-in-jwks"}),
]


class TestVerifier:
    def test_valid_token_returns_claims(self, verifier, token_factory):
        claims = verifier.verify(token_factory())

        assert claims["sub"] == "8b7c1d2e-0000-4000-8000-000000000001"
        assert claims["iss"] == ISSUER
        assert claims["custom:customer_id"] == "CUST-0042"

    @pytest.mark.parametrize("overrides", [case for _, case in INVALID_TOKENS], ids=[name for name, _ in INVALID_TOKENS])
    def test_rejects_invalid_token(self, verifier, token_factory, overrides):
        with pytest.raises(InvalidTokenError):
            verifier.verify(token_factory(**overrides))

    @pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c"])
    def test_rejects_malformed_token(self, verifier, token):
        with pytest.raises(InvalidTokenError):
            verifier.verify(token)

    def test_rejects_unsigned_token(self, verifier, token_factory):
        header, payload, _ = token_factory().split(".")
        with pytest.raises(InvalidTokenError):
            verifier.verify(f"{header}.{payload}.")

    def test_jwks_is_fetched_once_and_reused(self, verifier, jwks_client, token_factory):
        for _ in range(5):
            verifier.verify(token_factory())

        assert jwks_client.fetch_count == 1


class TestMeEndpoint:
    def test_returns_identity_and_customer_id(self, client, token_factory):
        response = client.get("/me", headers={"Authorization": f"Bearer {token_factory()}"})

        assert response.status_code == 200
        assert response.json() == {
            "sub": "8b7c1d2e-0000-4000-8000-000000000001",
            "email": "ana@example.com",
            "given_name": "Ana",
            "family_name": "Quispe",
            "customer_id": "CUST-0042",
        }

    def test_missing_customer_id_is_403(self, client, token_factory):
        response = client.get("/me", headers={"Authorization": f"Bearer {token_factory(**{'custom:customer_id': None})}"})

        assert response.status_code == 403
        assert response.json() == {"detail": "User is not linked to a customer"}

    def test_missing_token_is_401(self, client):
        response = client.get("/me")

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer not-a-jwt"])
    def test_malformed_header_is_401(self, client, header):
        response = client.get("/me", headers={"Authorization": header})

        assert response.status_code == 401

    @pytest.mark.parametrize("overrides", [case for _, case in INVALID_TOKENS], ids=[name for name, _ in INVALID_TOKENS])
    def test_invalid_token_is_401(self, client, token_factory, overrides):
        response = client.get("/me", headers={"Authorization": f"Bearer {token_factory(**overrides)}"})

        assert response.status_code == 401
        assert response.json() == {"detail": "Invalid or expired token"}
