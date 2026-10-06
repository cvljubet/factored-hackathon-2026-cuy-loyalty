# Evaluation reports

Per-case results of the routing and guardrail evaluations in `tests/evaluation`, kept so runs
can be compared across code changes, guardrail versions and router models.

- `reports/<name>.jsonl`: one run. Written by `EVAL_REPORT=<name> uv run pytest tests/evaluation`.
  The first line is the header: `run` (time, git commit) and `config`, a snapshot of every lever
  (`tests/evaluation/eval_config.py`):

  | Section | Levers | When |
  | --- | --- | --- |
  | `router` | the router prompt, `RouteResult` field descriptions (the model's output schema), output retries | always |
  | `rules` | keyword patterns in `agents/routing.py` and `agents/safety.py`, language word lists, the output-scan patterns | always |
  | `datasets` | hash and case count of each dataset | always |
  | `inference` | router model ID, region, temperature, max tokens, timeout, attempts, eval pacing | live |
  | `guardrail` | ID, version and the published definition: topics (definition, examples, actions), content filter strengths, PII entities, regexes, word lists, tiers | live |

  The guardrail definition comes from `GetGuardrail`, which the invoker role may not call: set
  `EVAL_GUARDRAIL_PROFILE=agents-account` (any profile that may read the guardrail). Without it the
  section records the error and the rest of the run is unaffected.
- `compare.py`: compares two runs: what changed in the levers (old -> new, list items added or
  removed, a line diff of the prompt), the pass rate per suite and per tag, then each case that changed.

```bash
uv run python evals/compare.py baseline guardrail-v2                       # names under reports/
uv run python evals/compare.py baseline guardrail-v2 --config-only         # only the lever diff
uv run python evals/compare.py baseline guardrail-v2 --fail-on-regression  # exit 1 on pass -> fail
```

Name reports after what changed (`guardrail-v2`, `router-prompt-examples`, `sonnet-router`).
`model_error` results (e.g. Bedrock throttling) are counted apart: they do not measure the
model, so a run with many of them should be repeated, not compared.

`reports/baseline-throttled.jsonl` is the first live run (2026-10-05, guardrail 3zqxnn5d43bs v1).
25 of its 42 router calls were throttled, so its `routing/hybrid` score is mostly the rule router.
It predates config snapshots, so compare.py shows no lever diff against it.
