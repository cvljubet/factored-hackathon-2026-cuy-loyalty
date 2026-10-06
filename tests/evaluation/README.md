# Evaluation Tests

Labelled datasets scored case by case, with a scorecard printed at the end of the run.

| Suite | What it scores | Runs |
| --- | --- | --- |
| `routing/rules` | `RuleBasedRouter`: engine, language, `credit_decision`, `sensitive_request` | always |
| `routing/hybrid` | `HybridRouter` over Bedrock Haiku (what `AGENT_ROUTER=bedrock` runs) | live |
| `routing/model` | the model's raw answer inside that run, before the rules are OR-ed in (report only) | live |
| `guardrail/scan` | the deterministic `scan_output` on assistant replies | always |
| `guardrail/bedrock` | the published Bedrock guardrail: block/allow, and which policy intervened | live |
| `guardrail/turn` | whole orchestrator turns with that guardrail: blocked input never reaches the router; replies are blocked by scan or guardrail | live |

Datasets live in `datasets/*.jsonl`, one case per line:

- `routing.jsonl`: `message`, expected `engine`, `language`, optional `credit_decision` / `sensitive_request`.
- `guardrail.jsonl`: `source` (`INPUT` or `OUTPUT`), `text`, `expect` (`block` / `allow`), optional
  `policy` (`topic:<name>`, `content:<type>`, `pii:<type>`) and `scan` (what `scan_output` should say).

Common fields: `tags` (grouped in the scorecard, e.g. `safety`, `false-block-trap`) and `known_gap`
(`{"<suite>": "<reason>"}`), which turns a known miss into a non-strict xfail for that suite.

## Running

Offline (no AWS):

```bash
uv run pytest tests/evaluation
```

Live, against the model account (run this directory on its own; `tests/unit` swaps in dummy AWS credentials):

```bash
EVAL_LIVE=1 BEDROCK_PROFILE=<model-account-profile> BEDROCK_REGION=us-east-2 \
BEDROCK_GUARDRAIL_ID=<id> BEDROCK_GUARDRAIL_VERSION=<n> \
EVAL_GUARDRAIL_PROFILE=agents-account EVAL_REPORT=baseline uv run pytest tests/evaluation -v
```

`terraform output` in `envs/dev-app` gives the guardrail ID and version. `EVAL_REPORT` is optional and writes each case's result
(including the intervening policies) to `evals/reports/<name>.jsonl`; compare two runs with `evals/compare.py` (see `evals/README.md`).

The model account allows 10 Haiku requests a minute, so Haiku calls are paced to `EVAL_MODEL_RPM` (default 9) and retried up to
`EVAL_MAX_ATTEMPTS` times in all (default 4); the routing suite takes about five minutes. A Haiku call that still fails is scored
`model_error`, never as the rule router's fallback answer.
