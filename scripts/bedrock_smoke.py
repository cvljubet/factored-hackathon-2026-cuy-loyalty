"""Live Bedrock smoke test: one inquiry, one router call and, if configured, one guardrail check.

Makes about four Bedrock requests with synthetic data. Never collected by pytest
(unit tests must not reach AWS). Reads the same BEDROCK_* settings as the backend,
from the environment or the repo-root .env; Cognito settings are not needed.

    BEDROCK_PROFILE=<profile> uv run python scripts/bedrock_smoke.py

Exits non-zero if any check fails.
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]  # the same import paths pytest uses

from agents.context import AgentContext  # noqa: E402
from agents.engines.inquiry import InquiryEngine  # noqa: E402
from agents.loyalty import GenericLoyaltyProvider  # noqa: E402
from agents.guardrails import NoOpGuardrail  # noqa: E402
from agents.model_router import ModelRouter  # noqa: E402
from agents.models import bedrock_router_model, bedrock_runtime_client  # noqa: E402
from app.chat.dependencies import bedrock_config, build_guardrail, build_inquiry_model  # noqa: E402
from app.config import Settings  # noqa: E402

CONTEXT = AgentContext(customer_id="SMOKE-0001", session_id="smoke-session", language="es")


class StaticProfiles:
    def get_profile(self, customer_id: str) -> dict[str, str]:
        return {"first_name": "Ana", "last_name": "Prueba", "city": "Cusco", "state": "Cusco", "country": "PE"}


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> int:
    # Errors inside the engines are logged rather than raised; show them.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings(cognito_region="-", cognito_user_pool_id="-", cognito_app_client_id="-", agent_llm="bedrock")
    config = bedrock_config(settings)
    print(f"region={config.region} profile={config.profile or '(default chain)'} inquiry={config.inquiry_model_id}")
    client = bedrock_runtime_client(config)
    results = []

    # 1-2. The inquiry agent on Bedrock must pick an existing tool and answer from it.
    engine = InquiryEngine(build_inquiry_model(settings, client), StaticProfiles(), GenericLoyaltyProvider())
    inquiry = engine.handle(CONTEXT, "¿En qué ciudad vivo según mi perfil?")
    results.append(
        check(
            "inquiry",
            inquiry.status == "answered" and "get_my_profile" in inquiry.tools_called,
            f"status={inquiry.status} tools={inquiry.tools_called} requests={inquiry.model_requests} "
            f"reply={inquiry.reply!r}",
        )
    )

    # 3. The model router alone: HybridRouter would hide a failure behind the rule-based fallback.
    try:
        route = ModelRouter(bedrock_router_model(config, client)).route(CONTEXT, "¿Cuánto gasté este mes?")
        results.append(check("router", True, repr(route)))
    except Exception as error:
        results.append(check("router", False, f"{type(error).__name__}: {error}"))

    # 4. The guardrail, when BEDROCK_GUARDRAIL_ID and BEDROCK_GUARDRAIL_VERSION are set.
    guardrail = build_guardrail(settings, client)
    if isinstance(guardrail, NoOpGuardrail):
        print("[SKIP] guardrail: BEDROCK_GUARDRAIL_ID / BEDROCK_GUARDRAIL_VERSION not set")
    else:
        verdict = guardrail.check_input(CONTEXT, "¿Cuál es el horario de la sucursal de Cusco?")
        results.append(check("guardrail", verdict.allowed, f"benign input -> {verdict}"))

    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
