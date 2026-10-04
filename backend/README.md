# Backend

FastAPI service for the Cuy Loyalty app. Dependencies live in the repo-root
`pyproject.toml` (managed with uv); the app package is `backend/app`.

## Layout

- `app/main.py`: app factory, CORS, router registration
- `app/config.py`: settings from environment variables / repo-root `.env`
- `app/auth.py`: Cognito JWT verification and the `get_current_user` dependency
- `app/routers/`: API routes

## Configuration

Copy `.env.example` (repo root) to `.env` and fill in the Cognito values from
`terraform -chdir=infrastructure/terraform/envs/dev output`:

| Variable | Description |
| --- | --- |
| `COGNITO_REGION` | User pool region, e.g. `us-east-2` |
| `COGNITO_USER_POOL_ID` | User pool ID |
| `COGNITO_APP_CLIENT_ID` | SPA app client ID; ID tokens must have it as `aud` |
| `COGNITO_JWKS_CACHE_SECONDS` | Optional, default 3600 |
| `CORS_ALLOW_ORIGINS` | Optional JSON list, default `["http://localhost:5173"]` |

## Authentication

Protected routes take the signed-in user as a dependency:

```python
from app.auth import CurrentUser

@router.get("/something")
def something(user: CurrentUser): ...
```

Requests must send a Cognito **ID token** as `Authorization: Bearer <token>`.
The token's signature is verified against the user pool JWKS (fetched once and
cached), along with `iss`, `exp`, `aud` (the app client ID) and `token_use == "id"`.
Any failure returns 401.

## Running

From the repo root:

```sh
uv sync
uv run uvicorn app.main:app --app-dir backend --reload --port 8000
```

Docs are at http://localhost:8000/docs.

## Tests

```sh
uv run pytest tests/unit/backend
```
