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
| `BEDROCK_PROFILE` | Optional AWS profile used **only** for Bedrock calls (e.g. a role in the account that runs the models); unset uses the default chain |
| `BEDROCK_INQUIRY_MODEL_ID` | Optional, default `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `BEDROCK_INQUIRY_FALLBACK_MODEL_ID` | Optional inquiry fallback, used when the primary model call fails, e.g. `us.anthropic.claude-sonnet-4-6` (Sonnet 5 and 5.5 are not available to our model account) |
| `BEDROCK_ROUTER_MODEL_ID` | Optional, default `us.anthropic.claude-haiku-4-5-20251001-v1:0` (keep a model that supports forced tool choice) |
| `BEDROCK_READ_TIMEOUT_SECONDS` | Optional, default `20`; per Bedrock request |
| `BEDROCK_MAX_ATTEMPTS` | Optional, default `2`; attempts per Bedrock request, the first one included |
| `BEDROCK_GUARDRAIL_ID`, `BEDROCK_GUARDRAIL_VERSION` | Optional; set both to check every message and reply with `ApplyGuardrail` (startup fails if only one is set) |

An empty variable counts as unset. Never put AWS keys in `.env`; use a profile
(`aws configure sso` or `aws configure`) locally and the ECS task role when deployed.

### Bedrock

With `AGENT_LLM=bedrock` the inquiry agent runs on Bedrock Converse; with
`AGENT_ROUTER=bedrock` routing does too, with the deterministic checks (human request,
credit decision, credit score or income) still applied on top and the rule-based router
as fallback. All Bedrock calls share one client (region, profile, timeouts).

When the guardrail is configured, an intervention blocks the message or reply. If the
guardrail can't be reached the turn is blocked too, and two such turns in a row hand
the customer to a human; interventions never do.

IAM permissions for whoever makes the calls (the ECS task role, or the role behind
`BEDROCK_PROFILE`): `bedrock:InvokeModel` on the inference profiles and their
foundation models in every region the profile routes to (us-east-1, us-east-2,
us-west-2), and `bedrock:ApplyGuardrail` on the guardrail when it's enabled.

Live check, a few Bedrock requests (unit tests never call AWS):

```sh
BEDROCK_PROFILE=<profile> BEDROCK_GUARDRAIL_ID=<id> BEDROCK_GUARDRAIL_VERSION=<n> \
  uv run python scripts/bedrock_smoke.py
```

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
