# Agents

Orchestration for the loyalty assistant. The FastAPI backend (`POST /chat`) is
the only caller; the frontend never talks to the agent directly. This package
has no web framework dependency.

Pydantic AI is used selectively: for the LLM-facing inquiry agent (tools and
tool loop) and the model router (structured output). The outer
orchestration stays plain application code.

## Flow of one turn (`orchestrator.py`)

1. Load session state (failure count, language) for `(customer_id, session_id)`.
2. Build an `AgentContext` with the authenticated `customer_id` passed in by the backend.
3. Input guardrail (`guardrails.py`: Bedrock `ApplyGuardrail` when configured, else no-op).
   An intervention blocks the message; an unreachable guardrail blocks it and counts as a failure.
4. Route (`routing.py`) to `inquiry | recommendation | escalation | out_of_scope`,
   with language, confidence, `sensitive_request` and `credit_decision`.
5. Deterministic rules first, with no model involved:
   - credit or eligibility decision: hand off to a human
   - explicit request for a human: hand off
   - credit score or income: fixed policy reply (`safety.py`, `messages.py`)
6. Otherwise run the engine:
   - inquiry: Pydantic AI agent with tools, at most 3 model rounds (`engines/inquiry.py`, `inquiry_agent.py`)
   - recommendation: model output if served, else "not available yet" (`engines/recommendation.py`)
   - out of scope: fixed reply
7. Output screening: the 8+ digit scan (`safety.scan_output`) plus the output guardrail.
8. A failed turn increments the failure count; the second failure in a row hands off.

## Layout

| Path | Contents |
| --- | --- |
| `context.py` | `AgentContext` (trusted identity, language, session, failure count) |
| `deps.py` | `AgentDeps`, the Pydantic AI dependency container |
| `serving.py` | `ServingRepository`: the DynamoDB customer-serving table (`DynamoServingRepository`) or the in-memory stand-in |
| `inquiry_agent.py` | The Pydantic AI `Agent` and its customer-scoped tool registrations |
| `tools.py` | Tool domain logic and `ToolResult`; `public()` strips keys and `bk_` fields from every result |
| `engines/` | Inquiry (runs the agent), recommendation (and its payload contract), escalation with handoff store |
| `routing.py` | `Router` protocol, `RouteResult`, `RuleBasedRouter` (default) |
| `model_router.py` | `ModelRouter` (structured output) and `HybridRouter` (model + deterministic checks); `AGENT_ROUTER=bedrock` |
| `models.py` | The shared Bedrock client (profile, timeouts) and Converse models (Haiku, optional Sonnet fallback); `AGENT_LLM=bedrock` |
| `local_model.py` | Deterministic `FunctionModel` so the app runs without model access |
| `guardrails.py`, `safety.py` | Guardrail protocol, no-op and Bedrock (`ApplyGuardrail`); sensitive-request and output-scan rules |
| `sessions.py`, `factory.py` | Session state store (in-memory); `build_orchestrator()` |

## Security invariants

- Tools read the customer only from `RunContext[AgentDeps]`. No tool declares a
  customer parameter (registration refuses one), and Pydantic AI rejects any
  argument a tool does not declare, so the model cannot pass or change `customer_id`.
- Recommendation payloads for any customer other than the context's are refused.
- A tool with no data returns `{"status": "unavailable"}` or an empty list; nothing is invented.
- Tool results never carry `PK`, `SK`, `customer_id` or `bk_` fields (`tools.public`).
- Tests set `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` and dummy AWS credentials
  (`tests/unit/conftest.py`), so no test can reach Bedrock; the guardrail is tested with botocore's `Stubber`.
