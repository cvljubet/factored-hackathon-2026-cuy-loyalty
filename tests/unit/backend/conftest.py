import os
import time
from collections.abc import Callable
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic_ai import models

# Any attempt to reach a real model (e.g. Bedrock) fails the test instead of calling AWS.
models.ALLOW_MODEL_REQUESTS = False

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
from app.customers.dependencies import RepositoryProfileSource, get_customer_repository, get_serving_repository  # noqa: E402
from app.customers.models import Customer  # noqa: E402
from app.customers.repository import InMemoryCustomerRepository  # noqa: E402
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


class RecordingCustomerRepository(InMemoryCustomerRepository):
    """An in-memory repository that records which customer_ids were looked up."""

    def __init__(self, customers: list[Customer]):
        super().__init__(customers)
        self.requested_ids: list[str] = []

    def get_customer(self, customer_id: str) -> Customer | None:
        self.requested_ids.append(customer_id)
        return super().get_customer(customer_id)


# The token's customer (custom:customer_id in id_token_claims) and another customer.
OWN_CUSTOMER = Customer(
    customer_id="CUST-0042",
    first_name="Ana",
    last_name="Quispe",
    email="ana@example.com",
    mobile_phone="+51 900 000 042",
    city="Cusco",
    state="Cusco",
    country="PE",
)
OTHER_CUSTOMER = Customer(
    customer_id="CUST-0099",
    first_name="Bruno",
    last_name="Other",
    email="bruno@example.com",
    mobile_phone="+55 11 90000 0099",
    city="Recife",
    state="Pernambuco",
    country="BR",
)


@pytest.fixture
def customer_repository() -> RecordingCustomerRepository:
    return RecordingCustomerRepository([OWN_CUSTOMER, OTHER_CUSTOMER])


@pytest.fixture
def client(verifier: CognitoTokenVerifier, customer_repository: RecordingCustomerRepository):
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_customer_repository] = lambda: customer_repository
    # SERVING_BACKEND=memory over the recording repository, for GET /me/profile.
    app.dependency_overrides[get_serving_repository] = lambda: RepositoryProfileSource(customer_repository)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def token_factory() -> Callable[..., str]:
    return make_token
