from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["es", "pt"]
LANGUAGES: tuple[Language, ...] = get_args(Language)
DEFAULT_LANGUAGE: Language = "es"


class AgentContext(BaseModel):
    """Everything a turn may know about who is asking.

    customer_id comes only from the verified Cognito identity, via the backend.
    It is never read from the request body or from model output, and tools take
    it from here (through AgentDeps) rather than from their arguments.
    """

    model_config = ConfigDict(frozen=True)

    customer_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    language: Language
    # Failed turns in a row in this session; two trigger a handoff to a human.
    consecutive_failures: int = Field(default=0, ge=0)
