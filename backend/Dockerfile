# syntax=docker/dockerfile:1
# Backend API image (FastAPI + agents). Build from the repository root:
#   docker build -f backend/Dockerfile -t cuy-loyalty-backend .
# Configuration (Cognito, CORS, ...) comes from environment variables at run time;
# nothing secret is baked into the image.

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Runtime dependencies only, exactly as pinned in uv.lock (no dev/test or notebook groups).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-default-groups --no-install-project


FROM python:${PYTHON_VERSION}-slim AS runtime

RUN useradd --system --uid 10001 --no-create-home app

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY backend/app /app/backend/app
COPY agents /app/agents

# /app on PYTHONPATH makes the top-level agents package importable next to backend/app.
# AGENT_LLM=local: the deterministic stand-in model, so no Bedrock access is needed yet.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYDANTIC_AI_NO_BANNER=1 \
    AGENT_LLM=local \
    AGENT_ROUTER=rules

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

# One worker on purpose: sessions and handoffs are in memory, so they must live in a single process.
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
