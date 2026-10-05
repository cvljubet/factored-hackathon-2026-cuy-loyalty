from fastapi.testclient import TestClient

from app.auth import get_token_verifier
from app.main import app


def test_health_is_200_without_authentication():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_does_not_touch_token_verification():
    def fail():
        raise AssertionError("health must not verify tokens")

    app.dependency_overrides[get_token_verifier] = fail
    try:
        assert TestClient(app).get("/health").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_health_ignores_a_bad_token():
    response = TestClient(app).get("/health", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 200


def test_protected_routes_still_require_a_token():
    client = TestClient(app)

    assert client.get("/me").status_code == 401
    assert client.get("/me/profile").status_code == 401
    assert client.post("/chat", json={"message": "hola"}).status_code == 401
