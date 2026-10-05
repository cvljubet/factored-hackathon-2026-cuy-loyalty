"""A deterministic stand-in model so POST /chat works locally before Bedrock exists.

A Pydantic AI FunctionModel: it picks at most one tool by keyword, then words the
tool result with a template. It only ever repeats tool data, and says so when a
tool is unavailable.
"""

from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.context import Language
from agents.text import compile_patterns, matches_any, normalize

_TOOL_KEYWORDS = [
    ("get_my_profile", [r"\bperfil\b", r"\b(mis|meus) (datos|dados)\b", r"\b(mi nombre|meu nome)\b", r"\b(mi ciudad|minha cidade)\b"]),
    ("get_my_agent", [r"\bejecutiv", r"\bgerente\b", r"\basesor"]),
    ("get_my_spending", [r"\bgast", r"\bconsumo"]),
    ("get_my_campaigns", [r"\bcampan", r"\bpromoc", r"\boferta", r"\bbeneficio", r"\b(puntos|pontos|millas|milhas)\b"]),
    ("get_my_transactions", [r"\btransac", r"\bmovimiento", r"\bmovimentac", r"\bcompras?\b"]),
    ("get_my_contacts", [r"\bcontacto", r"\bcontato", r"\bllamad", r"\bligac"]),
    ("get_my_complaints", [r"\breclamo", r"\bqueja", r"\breclamac"]),
    ("get_branch_info", [r"\bsucursal", r"\bagencia", r"\boficina", r"\bhorario"]),
    ("get_exchange_rate", [r"\bcambio\b", r"\bcotizacion", r"\bcotacao", r"\bdolar", r"\beuros?\b"]),
    ("get_my_products", [r"\bproduct", r"\bproduto", r"\btarjeta", r"\bcarto", r"\bcartao", r"\bcuenta", r"\bcontas?\b", r"\bsaldo"]),
]
_COMPILED = [(name, compile_patterns(patterns)) for name, patterns in _TOOL_KEYWORDS]

_TEMPLATES: dict[str, dict[Language, str]] = {
    "profile": {"es": "Estos son los datos de tu perfil: {details}.", "pt": "Estes são os dados do seu perfil: {details}."},
    "data": {"es": "Esto es lo que encontré: {details}.", "pt": "Isto é o que encontrei: {details}."},
    "unavailable": {
        "es": "Esa información todavía no está disponible para mí. Pronto podré consultarla.",
        "pt": "Essa informação ainda não está disponível para mim. Em breve poderei consultá-la.",
    },
    "help": {
        "es": "Puedo ayudarte con tu perfil, productos, beneficios, gastos y sucursales. ¿Qué quieres consultar?",
        "pt": "Posso ajudar com seu perfil, produtos, benefícios, gastos e agências. O que você quer consultar?",
    },
}


def local_model() -> FunctionModel:
    return FunctionModel(_respond, model_name="local-deterministic")


def _respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    # The agent's instructions name the reply language (agents.prompts).
    language: Language = "pt" if "Portuguese" in (info.instructions or "") else "es"
    last = messages[-1]
    returns = [part for part in last.parts if isinstance(part, ToolReturnPart)] if isinstance(last, ModelRequest) else []
    if returns:
        return ModelResponse(parts=[TextPart(" ".join(_describe(part, language) for part in returns))])

    prompt = _latest_user_prompt(messages)
    available = {tool.name for tool in info.function_tools}
    text = normalize(prompt)
    for name, patterns in _COMPILED:
        if name in available and matches_any(text, patterns):
            return ModelResponse(parts=[ToolCallPart(name, {}, tool_call_id="local-1")])
    return ModelResponse(parts=[TextPart(_TEMPLATES["help"][language])])


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _describe(part: ToolReturnPart, language: Language) -> str:
    content: Any = part.content
    result = content.model_dump() if hasattr(content, "model_dump") else dict(content or {})
    data: dict[str, Any] = dict(result.get("data") or {})
    if result.get("status") != "ok" or not data:
        return _TEMPLATES["unavailable"][language]
    if part.tool_name == "get_my_profile":
        name = " ".join(str(data[key]) for key in ("first_name", "last_name") if data.get(key))
        place = [str(data[key]) for key in ("city", "state", "country") if data.get(key)]
        details = ", ".join([name, *place] if name else place)
        return _TEMPLATES["profile"][language].format(details=details)
    details = "; ".join(f"{key}: {value}" for key, value in data.items())
    return _TEMPLATES["data"][language].format(details=details)
