"""Cognito JWT authentication as a reusable FastAPI dependency.

Routes that need the signed-in user declare ``user: CurrentUser`` (or
``Depends(get_current_user)``); every token is verified against the user
pool's JWKS before any claim is trusted.
"""

import logging
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

# Cognito signs user pool tokens with RS256 only; pinning it blocks alg-confusion attacks.
ALGORITHMS = ["RS256"]
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "token_use"]


class InvalidTokenError(Exception):
    """The token is missing, malformed, expired, or fails verification."""


class AuthenticatedUser(BaseModel):
    """Identity claims taken from a verified Cognito ID token."""

    sub: str
    email: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    # From custom:customer_id; links the user to their record in the customer dataset.
    customer_id: str | None = None


class CognitoTokenVerifier:
    """Verifies Cognito ID tokens issued to one app client of one user pool."""

    def __init__(self, issuer: str, client_id: str, jwks_client: jwt.PyJWKClient):
        self.issuer = issuer
        self.client_id = client_id
        self.jwks_client = jwks_client

    def verify(self, token: str) -> dict[str, Any]:
        """Return the token's claims, or raise InvalidTokenError.

        Raises PyJWKClientConnectionError if the JWKS cannot be fetched; that is
        an outage, not a bad token.
        """
        try:
            # Looks up the key by the header's kid in the cached JWKS.
            signing_key = self.jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=ALGORITHMS,
                audience=self.client_id,
                issuer=self.issuer,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWKClientConnectionError:
            raise
        except jwt.PyJWTError as error:
            raise InvalidTokenError(str(error)) from error

        # Access tokens are signed by the same keys but carry no aud and no
        # custom attributes; only ID tokens are accepted here.
        if claims["token_use"] != "id":
            raise InvalidTokenError(f"token_use is {claims['token_use']!r}, expected 'id'")
        return claims


@lru_cache
def get_token_verifier() -> CognitoTokenVerifier:
    """One verifier per process, so the JWKS is fetched once and then reused."""
    settings = get_settings()
    jwks_client = jwt.PyJWKClient(
        settings.cognito_jwks_url,
        cache_jwk_set=True,
        lifespan=settings.cognito_jwks_cache_seconds,
        timeout=5,
    )
    return CognitoTokenVerifier(settings.cognito_issuer, settings.cognito_app_client_id, jwks_client)


# auto_error=False so a missing header gets the same 401 as any other bad token.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# A sync dependency: FastAPI runs it in a worker thread, so a JWKS fetch does not block the event loop.
def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[CognitoTokenVerifier, Depends(get_token_verifier)],
) -> AuthenticatedUser:
    if credentials is None:
        raise _unauthorized("Missing bearer token")

    try:
        claims = verifier.verify(credentials.credentials)
    except InvalidTokenError as error:
        logger.info("Rejected token: %s", error)
        raise _unauthorized("Invalid or expired token") from error
    except jwt.PyJWKClientConnectionError as error:
        logger.error("Could not fetch Cognito JWKS: %s", error)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is temporarily unavailable") from error

    return AuthenticatedUser(
        sub=claims["sub"],
        email=claims.get("email"),
        given_name=claims.get("given_name"),
        family_name=claims.get("family_name"),
        customer_id=claims.get("custom:customer_id"),
    )


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
