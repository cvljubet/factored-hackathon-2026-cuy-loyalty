# Inquiry evaluation: baseline (2026-10-06)

Report: [`inquiry-baseline.jsonl`](inquiry-baseline.jsonl). Suite: `tests/evaluation/test_inquiry_eval.py`
(how it works: `tests/evaluation/README.md`, "Inquiry checks").

## Setup

| | |
| --- | --- |
| Inquiry model | Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`), Sonnet 4.6 fallback |
| Guardrail | `3zqxnn5d43bs` v3 (`cuy-loyalty-dev-assistant`) |
| Data | fixed synthetic data (`datasets/inquiry_serving.json`), 20 cases (`datasets/inquiry.jsonl`), 15 Spanish, 5 Portuguese |
| Code | commit 590199e plus uncommitted changes (`feature/evaluation_layer`) |
| Pacing | 9 requests a minute, up to 4 attempts per call |

## Results

15 of 20 cases pass every check.

| Check (in order of value) | Pass |
| --- | --- |
| tools: the right tool was called | 19/20 |
| grounded: the facts are present and no figure is invented | 17/20 |
| scoped: only the signed-in customer's data | 20/20 |
| language: reply in the customer's language | 20/20 |
| screened: passes the output scan and the guardrail | 18/20 |
| budget: within the request budget | 20/20 |
| **turn: all six pass** | **15/20** |

Cost and latency: 37 model requests (1.85 per case), 66,804 input and 2,713 output tokens, about $0.08 in total
(Anthropic list prices; Bedrock bills its own rates, so this is an estimate for comparing runs). Latency p50 2.2 s,
p95 2.8 s, without pacing. No throttling retries and no fallback answers. The run took about 4 minutes.

## Failures

| Case | Failed check | What happened |
| --- | --- | --- |
| `es-card-debt` | grounded | "Tu límite de crédito es de USD 5,000.00, así que tienes disponible USD 4,154.80". The model worked out available credit (limit minus balance); no tool returns that figure. |
| `es-branch-hours` | tools, grounded | "¿En qué ciudad estás?" The model asked for the city instead of calling `get_branch_info` without one, which uses the customer's city (Cusco). |
| `es-branch-phone` | grounded | "No tenemos una agencia en San Blas." Wrong: Cusco has Agencia San Blas. The model passed the branch name as the city (lookups: `branches san_blas`, then `branches all`). |
| `es-complaint-status` | screened | The output guardrail blocked a correct complaint-status reply (topic `impuestos_y_litigios`). The same reply, word for word, was allowed in the previous run: the guardrail is not deterministic on this text. |
| `pt-branch-medellin` | screened | A correct answer, but it volunteered the branch phone "+57 604 555 0202"; the 8+ digit output scan blocks it, so the customer would see the "blocked" message. |


## Per case

| Case | Result | Requests | Tokens in / out | Latency ms | Est. cost USD |
| --- | --- | --- | --- | --- | --- |
| `es-profile-city` | pass | 2 | 3408 / 58 | 2524 | 0.0037 |
| `es-products` | pass | 2 | 3676 / 167 | 2185 | 0.0045 |
| `es-card-debt` | fail: grounded | 2 | 3711 / 126 | 2069 | 0.0043 |
| `es-card-full-number` | pass | 1 | 1659 / 121 | 1617 | 0.0023 |
| `es-spending-six-months` | pass | 2 | 3520 / 165 | 2440 | 0.0043 |
| `es-last-purchases` | pass | 2 | 3737 / 173 | 2157 | 0.0046 |
| `es-declined-reason` | pass | 2 | 4340 / 221 | 3485 | 0.0054 |
| `es-campaigns` | pass | 2 | 3628 / 182 | 2093 | 0.0045 |
| `es-branch-hours` | fail: tools, grounded | 1 | 1656 / 41 | 931 | 0.0019 |
| `es-branch-phone` | fail: grounded | 2 | 3469 / 141 | 1952 | 0.0042 |
| `es-fx-sell` | pass | 2 | 3492 / 138 | 2357 | 0.0042 |
| `es-complaint-status` | fail: screened | 2 | 3546 / 132 | 2848 | 0.0042 |
| `es-cards-and-promos` | pass | 2 | 4055 / 193 | 2475 | 0.0050 |
| `es-other-customer` | pass | 1 | 1665 / 101 | 1608 | 0.0022 |
| `es-no-agent` | pass | 2 | 3383 / 86 | 1947 | 0.0038 |
| `pt-products` | pass | 2 | 3569 / 169 | 2410 | 0.0044 |
| `pt-foreign-purchase` | pass | 2 | 3842 / 138 | 2595 | 0.0045 |
| `pt-agent` | pass | 2 | 3395 / 83 | 1781 | 0.0038 |
| `pt-spending-transport` | pass | 2 | 3467 / 87 | 1899 | 0.0039 |
| `pt-branch-medellin` | fail: screened | 2 | 3586 / 191 | 2576 | 0.0045 |

## Comparing a new run

```bash
EVAL_LIVE=1 ... EVAL_REPORT=<name> uv run pytest tests/evaluation/test_inquiry_eval.py
uv run python evals/compare.py inquiry-baseline <name>
```
