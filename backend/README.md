# Backend

FastAPI service for the Cuy Loyalty app. Dependencies live in the repo-root
`pyproject.toml` (managed with uv); the app package is `backend/app`.

## Layout

- `app/main.py`: app factory, CORS, router registration
- `app/config.py`: settings from environment variables / repo-root `.env`
- `app/auth.py`: Cognito JWT verification and the `get_current_user` / `get_current_customer_id` dependencies
- `app/customers/`: customer serving
  - `models.py`: `Customer` (field names match gold `customer_360`) and the `CustomerProfile` response
  - `repository.py`: the `CustomerRepository` interface and `InMemoryCustomerRepository`
  - `service.py`: profile lookup logic, independent of storage
  - `dependencies.py`: picks the repository implementation (`CUSTOMER_REPOSITORY`)
  - `fake_data.py`: synthetic customers served until DynamoDB exists
- `app/chat/dependencies.py`: builds the agent orchestrator (from the top-level `agents/` package) and adapts `CustomerRepository` to its profile tool
- `app/routers/`: API routes (`GET /me`, `GET /me/profile`, `POST /chat`)

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
| `CUSTOMER_REPOSITORY` | Optional, default `memory` (synthetic data); DynamoDB comes later |
| `AGENT_LLM` | Optional: `local` (default, deterministic stand-in, no AWS) or `bedrock` (Converse via Pydantic AI) |
| `AGENT_ROUTER` | Optional: `rules` (default) or `bedrock` (Haiku structured output OR-ed with the deterministic checks) |
| `BEDROCK_REGION` | Optional, default `us-east-2`; credentials come from the standard AWS chain |
| `BEDROCK_INQUIRY_MODEL_ID` | Optional, default `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `BEDROCK_INQUIRY_FALLBACK_MODEL_ID` | Optional, e.g. `us.anthropic.claude-sonnet-5-5` |
| `BEDROCK_ROUTER_MODEL_ID` | Optional, default `us.anthropic.claude-haiku-4-5-20251001-v1:0` |

## Customer data

Customer routes never take a `customer_id` from the request. They depend on
`CurrentCustomerId`, which comes only from the verified token's
`custom:customer_id` (403 when absent), and pass it to the `CustomerRepository`.

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
uv run python -m uvicorn app.main:app --app-dir backend --reload --port 8000
```

Use `python -m uvicorn` (not plain `uvicorn`): it puts the repo root on the import
path, which the backend needs to import the top-level `agents` package.

Docs are at http://localhost:8000/docs.

## Docker (ECS Fargate)

Build from the repository root (the context must include `backend/` and `agents/`):

```sh
docker build --platform linux/amd64 -f backend/Dockerfile -t cuy-loyalty-backend .
docker run --rm -p 8000:8000 \
  -e COGNITO_REGION=us-east-2 -e COGNITO_USER_POOL_ID=<pool id> -e COGNITO_APP_CLIENT_ID=<client id> \
  cuy-loyalty-backend
```

The image installs only runtime dependencies from `uv.lock`, runs as a non-root
user on port 8000 with a single Uvicorn worker (sessions and handoffs are in
memory), and holds no configuration or credentials. `GET /health` is the
unauthenticated health check. The container exits at startup if the Cognito
variables are missing.

## Tests

```sh
uv run pytest tests/unit/backend tests/unit/agents
```
