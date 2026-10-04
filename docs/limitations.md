# Limitations

Known limits of the LATAM Bank dataset that the pipeline reports but can't fix, and what each one means
for the features and the advisor. Figures come from profiling the silver tables on 2026-10-04
(`notebooks/reports/`); the pipeline's own reports (`dq_summary` in Athena) give the current numbers.

## Source data defects

Foreign keys the data dictionary declares but the data doesn't honour. They are marked
`known_broken` in `data/pipelines/glue/dq_rules.py`: `silver/_fk_report/` still shows their orphan
rate (status `known_broken`), but they never fail the pipeline and rows get no orphan flag.

| Column | What the data shows (2026-10-04) | Consequence |
|---|---|---|
| `customers.registration_branch_id` | 150,000 distinct values for 150,000 customers and 350 branches; 99.997% match no branch | A customer's registration branch is unknown |
| `service_agents.assigned_branch_id` | 833 distinct values for 350 branches; 99.8% match no branch | An agent's branch is unknown; routing can't use it |
| `complaints.origin_interaction_id` | Empty in every row (`all_null` in `silver/_rule_report/`, `not_checkable` in `_fk_report`) | A complaint can't be linked to the call that started it |

## Synthetic content with little signal

| Issue | Evidence | Consequence |
|---|---|---|
| Call transcripts are templates | 42 distinct `customer_text` and 42 `agent_text` values across 171,321 transcripts; agent lines keep unfilled placeholders such as `{monto} {moneda}` and `{limite}`; `detected_intents` is always `consulta_general` | Transcripts can't support NLP features or quoted examples; we use the structured fields (`contact_reason`, sentiment, `entities_*`) instead |
| Complaint categories are uniform | 5 categories with 13,194 to 13,580 complaints each; the description is always "Queja relacionada con <category>" | Complaint category carries no information about the customer; only complaint counts are used |
| Templated campaign subjects | Every subject is "¡Oferta especial en <product>!"; 38,142 sends say "en nan", a missing product written out as text | Subjects aren't used as features |
| One geographic zone | All 350 branches are "Urbana" (the dictionary lists Urban, Suburban, Rural) | Zone can't segment branches |

## Coverage gaps

| Issue | Evidence | Consequence |
|---|---|---|
| No products in MXN | Products are in USD (220,501), COP (107,975) or ARS (71,524), while 74,907 of the 150,000 customers are Mexican | Mexican customers hold USD products; balances and spend are compared in USD (`current_balance_usd`, `amount_usd`) |
| `merchant_category` mostly missing | 76.8% of transactions (3,396,215) have none | Spending by category uses `transaction_category` instead |
| No NPS Promoters | NPS scores only go up to 7, so there are Detractors (0 to 6) and Passives (7) and no Promoters (9 to 10) | NPS can only separate unhappy from neutral customers; CSAT is the satisfaction feature |
| Transaction coordinates | 80.6% of transactions have no coordinates, and many of those that do lie within 1° of (0, 0), "null island" (the `location` rule in `dq_summary` gives the share) | Those transactions are flagged `location`; coordinates aren't used for features |
| Fewer rows than the dictionary says | Transactions 4.43M of 5M, interactions 686k of 800k, transcripts 171k of 200k, surveys 213k of 250k, complaints 67k of 80k, campaign sends 1.75M of 2M (11% to 16% below) | Reported in `silver/_volume_report/`; the dictionary's counts look rounded up, not like missing files |

## Dictionary and data disagree

| Issue | What the data does | How the pipeline handles it |
|---|---|---|
| Labels in two languages | Product types are Spanish ("Tarjeta Crédito"); interaction sentiment is Spanish ("Negativo") while survey sentiment is English ("Negative"); the dictionary lists English | Allowed values in the contracts use the data's labels (`docs/data-contracts.md`) |
| Extra values | Interactions use a "Web" channel the dictionary doesn't list | Accepted, noted in `dq_rules.py` |
| Duplicated column | `reason_category` repeats `contact_reason` value for value | Dropped in silver |
| Exchange-rate row count | The dictionary says 3,000 rows; the table has every pair every day, 1,097 x 12 = 13,164 | `EXPECTED_ROWS` uses 13,164 |
