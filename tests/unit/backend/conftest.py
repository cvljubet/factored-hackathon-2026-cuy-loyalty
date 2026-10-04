import os
import time
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

REGION = "us-east-2"
USER_POOL_ID = "us-east-2_TestPool"
CLIENT_ID = "test-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
KID = "test-key-1"

# Set before app.main is imported, since it builds the app (and reads settings) at import time.
os.environ.update(
    COGNITO_REGION=REGION,
    COGNITO_USER_POOL_ID=USER_POOL_ID,
    COGNITO_APP_CLIENT_ID=CLIENT_ID,
)

from app.auth import CognitoTokenVerifier, get_token_verifier  # noqa: E402
from app.main import app  # noqa: E402


def _new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY = _new_key()
OTHER_KEY = _new_key()


class StubJWKClient(jwt.PyJWKClient):
    """A real PyJWKClient whose HTTP fetch returns a local JWKS and counts calls."""

    def __init__(self, jwks: dict[str, Any]):
        super().__init__(f"{ISSUER}/.well-known/jwks.json", cache_jwk_set=True, lifespan=3600)
        self._jwks = jwks
        self.fetch_count = 0

    def fetch_data(self) -> Any:
        self.fetch_count += 1
        self.jwk_set_cache.put(self._jwks)
        return self._jwks


def _jwks() -> dict[str, Any]:
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(SIGNING_KEY.public_key(), as_dict=True)
    return {"keys": [{**public_jwk, "kid": KID, "alg": "RS256", "use": "sig"}]}


def id_token_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims = {
        "sub": "8b7c1d2e-0000-4000-8000-000000000001",
        "aud": CLIENT_ID,
        "iss": ISSUER,
        "token_use": "id",
        "iat": now,
        "auth_time": now,
        "exp": now + 3600,
        "email": "ana@example.com",
        "email_verified": True,
        "given_name": "Ana",
        "family_name": "Quispe",
        "custom:customer_id": "CUST-0042",
    }
    claims.update(overrides)
    return {key: value for key, value in claims.items() if value is not None}


def make_token(key: rsa.RSAPrivateKey = SIGNING_KEY, kid: str = KID, **overrides: Any) -> str:
    """A signed ID token; pass a claim as None to omit it."""
    return jwt.encode(id_token_claims(**overrides), key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def jwks_client() -> StubJWKClient:
    return StubJWKClient(_jwks())


@pytest.fixture
def verifier(jwks_client: StubJWKClient) -> CognitoTokenVerifier:
    return CognitoTokenVerifier(ISSUER, CLIENT_ID, jwks_client)


@pytest.fixture
def client(verifier: CognitoTokenVerifier):
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def token_factory() -> Callable[..., str]:
    return make_token
