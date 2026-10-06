import time
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agents.context import Language
from agents.engines.base import ResultStatus
from agents.routing import Engine
from agents.sessions import SessionConflict
from app.auth import CurrentCustomerId
from app.chat.dependencies import OrchestratorDep
from app.observability import log_turn

router = APIRouter()


class ChatRequest(BaseModel):
    """What the client may send: its message and session details, never a customer_id."""

    # Unknown fields (e.g. customer_id) are rejected with 422 rather than silently ignored.
    model_config = ConfigDict(extra="forbid")

    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")
    # The UI language; the router may still answer in the language the customer writes in.
    language: Language | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    engine: Engine
    language: Language
    status: ResultStatus
    escalated: bool
    handoff_id: str | None = None


# Sync on purpose: LLM and data calls block, so FastAPI runs this in a worker thread.
@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, customer_id: CurrentCustomerId, orchestrator: OrchestratorDep) -> ChatResponse:
    """One assistant turn for the signed-in customer."""
    session_id = body.session_id or uuid.uuid4().hex
    start = time.perf_counter()
    try:
        reply = orchestrator.handle(
            customer_id=customer_id,
            session_id=session_id,
            user_message=body.message,
            language_hint=body.language,
        )
    except SessionConflict:
        # Another request (a double submit or a second tab) saved this session first; nothing was saved.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The conversation changed meanwhile; send your message again."
        ) from None
    log_turn(reply, (time.perf_counter() - start) * 1000)
    return ChatResponse(
        session_id=reply.session_id,
        reply=reply.reply,
        engine=reply.engine,
        language=reply.language,
        status=reply.status,
        escalated=reply.escalated,
        handoff_id=reply.handoff_id,
    )
