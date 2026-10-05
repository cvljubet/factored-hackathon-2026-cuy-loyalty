# Architecture

## Overview

### Data lake

One S3 bucket with three layers, built by two Glue jobs chained in a workflow
(`data/pipelines/glue/`, details in `data/pipelines/README.md`):

```
bronze/   raw CSVs as delivered                        ─┐ bronze_to_silver.py
silver/   typed, deduplicated, every row kept and        ┘
          flagged (dq_reasons, dq_is_valid)             ─┐ silver_to_gold.py
gold/     ML and analytics zone          (<prefix>_gold)  │  rows left out per EXCLUDE_WHEN,
gold/agent/   agent zone                 (<prefix>_agent)┘  counted in gold/_exclusions/
```

Gold has two zones with different readers:

| Zone | Tables | Read by |
|---|---|---|
| `gold/` | `customer_features`, `product_catalog`, `fx_daily`, `_exclusions` | the recommender, dashboards, notebooks |
| `gold/agent/` | `customer_360`, `campaigns`, `transactions_recent`, `contacts_recent`, `complaints`, `branches`, `fx_latest` | the assistant's tools (one table per tool) and, later, the export to the serving store |

`gold/recommendations` is written by the ranking model. Its customer-facing part belongs in the agent
zone too.

## Key Decisions

### The assistant reads only the agent zone

The agent zone holds only what the assistant may say to the customer it's talking to. The rest of gold
(credit score, income, age, days past due, escalation rate) stays in the ML zone. Access rules enforce the
split, so it doesn't depend on every tool filtering columns correctly:

- Terraform's catalog module creates `<prefix>-agent-zone-read`, a policy that can list and read
  `gold/agent/*` and the `<prefix>_agent` Glue database, and nothing else in the lake. The backend's ECS
  task role attaches this policy, and only this policy, for lake access. A bug in a tool can't return
  `credit_score`, because that column doesn't exist anywhere the role can read.
- Every agent table names its columns explicitly, so a column added to silver later never reaches the
  agent by accident.
- `tests/integration/test_agent_gold.py` fails if any customer-level agent table holds a column from
  `AGENT_FORBIDDEN` (`silver_to_gold.py`), nested fields included.

### Masked in gold, not in the tools

Email (`m***@mail.com`), mobile phone and product numbers (`****1234`) are masked when gold is built, so
raw values never reach the agent zone or a serving store copied from it. A transaction on a product that
belongs to someone else (`product_owner_mismatch` in silver) shows no product number. Expiration dates
show month and year only. The number of days past due becomes a backend-only flag (`bk_is_overdue`).
Document number, address and date of birth aren't in the agent zone.

### `bk_` marks backend-only columns

In agent tables, columns the model may read and quote have plain names. Columns only the backend needs
(ids, segment, routing and targeting fields, coordinates, the mid exchange rate) start with `bk_`. Before
building a tool result, each tool removes every `bk_` key, nested ones included. One small helper
enforces the rule, and the column names document it. The ids the customer may quote are their own
`customer_id` (the lookup key) and `complaint_id`. A test checks that every other `*_id` column starts
with `bk_`.

### Every customer reaches the agent zone

Gold never drops a customer, even one with quality flags (for example a `last_updated` after the
dataset's end): the assistant must find every customer who logs in. Rows that hang off a customer are
left out when their customer or product doesn't exist, or when they're dated outside the dataset
(`EXCLUDE_WHEN` in `silver_to_gold.py`). Without that, a complaint dated 2027 would reach the
assistant, count in features and move the 90-day window.

### Fairness of what the model uses and what the assistant says

`customer_features` includes `age`, `credit_score` and income as features for the recommender. When the
assistant explains a recommendation, it cites only the customer's own spending categories
(`spend_usd_*` in `agent/customer_360`), never those features. The agent zone has none of them, so the
assistant couldn't cite them anyway. Age as a feature can favour or exclude age groups. Before the
recommender's results are used, its evaluation should compare acceptance and ranking across age bands.
