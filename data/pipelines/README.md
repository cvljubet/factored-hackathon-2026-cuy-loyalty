# Data Pipelines

Place data ingestion, validation, and transformation pipeline code here.

## Glue jobs (`glue/`)

| Job | Reads | Writes |
|---|---|---|
| `bronze_to_silver.py` | `bronze/<table>/ingest_date=*/` raw CSVs | `silver/<table>/` Parquet, typed, deduplicated on the primary key, quality flags (below); `silver/_dq_report/` row counts, `silver/_rule_report/` failing rows per rule, `silver/_fk_report/` orphans per foreign key |
| `silver_to_gold.py` | silver tables | ML zone: `gold/product_catalog`, `gold/customer_features`, `gold/fx_daily`; agent zone: `gold/agent/<table>` (below); `gold/_exclusions/` rows left out per table and reason |

`gold/recommendations` is written by the ranking model, not by these jobs.

### Gold zones

`gold/` (Glue database `--gold_db`) is for the model and analytics. `gold/agent/` (`--agent_db`) is
everything the assistant can read: one table per tool, and its role has no access to the rest
(`docs/architecture.md`).

| Agent table | One row per | Notes |
|---|---|---|
| `customer_360` | customer (all of them) | masked email and phone; `products` with masked numbers; 90-day spend; the agent of the latest contact |
| `campaigns` | delivered send | `valid_on_as_of`: the offer is running on the as-of date |
| `transactions_recent` | last 20 transactions per customer | declined ones included; amount in USD filled from the daily rate |
| `contacts_recent` | last 10 call-center contacts per customer | |
| `complaints` | complaint | |
| `branches` | branch | public |
| `fx_latest` | currency pair | buy and sell on the as-of date (or the latest day before it with a rate); public |

The as-of date is the latest transaction's day (2026-06-17), never today. In agent tables, backend-only
columns (ids, segment, routing, coordinates) start with `bk_`, and the tools strip them before the model
sees a result. `AGENT_FORBIDDEN` lists customer columns no agent table may hold;
`tests/integration/test_agent_gold.py` checks it.

### Data quality

Every bronze file must have exactly the header in `COLUMNS` (`bronze_to_silver.py`): the data
dictionary's columns, in its order. A file with a column added, missing, renamed or moved fails the
job, and the error names the file. When the source schema really changes, update `COLUMNS` (and
`TABLES` for the types) in the same change that handles the new column.

Duplicates are the one thing silver removes: one row per primary key, always the same one (latest
ingest, then latest `last_updated` not after the dataset's end, then latest `process_date`, then a hash
of the row). `dq_copies` and `dq_versions` on the kept row say how many bronze rows shared its key and
how many distinct contents they had; `silver/_dq_report/` splits the removed rows into exact and
conflicting duplicates. Rows with no key are kept, one each, flagged `dq_invalid_missing_key`.

Silver never drops a row for quality. Each broken rule sets a boolean `dq_invalid_<rule>` column,
`dq_reasons` lists the rules a row breaks (e.g. `["orphan_customers"]`) and `dq_is_valid` is true when it
breaks none. Gold decides which reasons keep a row out (`EXCLUDE_WHEN` in `silver_to_gold.py`: an
orphan customer or product, or an event dated outside the dataset) and counts them. Customers are never
left out.

The rules themselves live in `dq_rules.py`, which Terraform uploads next to the jobs. Today that is the
24 foreign keys from the data dictionary: every child table gets `dq_invalid_orphan_<parent>` per key,
and the silver job fails when a key has more than 5% orphans (warns above 1%). Tables are built parents
first for that reason, so with `--tables` the parents must already be in silver.

Table-specific rules (`table_rules.py`, one function per table) add more flags, fill gaps in place
with a `<column>_imputed` marker (e.g. `was_opened`, `nps_category`, `amount_usd` on USD transactions),
and derive columns next to the originals (`entities_*`, `income_currency`, campaign name codes). One
imputation needs another table, so it lives in `bronze_to_silver.py`: campaign subjects written as
"¡Oferta especial en nan!" get the campaign's `promoted_product` (`subject_imputed`), or become null
when the campaign has none. The
exchange-rate table is completed to every pair and day; more than 3 missing days in a row for a pair
fails the job. `silver/_rule_report/` also counts imputations (`imputed:<column>`) and lists source
columns that are empty in every row (`all_null:<column>`, e.g. `complaints.origin_interaction_id`).

Generic rules come from the data contracts in `contracts.py`: one Pandera schema per silver table,
built from the dictionary (NOT NULL, UNIQUE) and `dq_rules.py` (allowed values, ranges, event dates
inside the dataset, date order). The same schema flags rows (`required_missing`, `not_allowed`,
`out_of_range`, `outside_dataset`, `date_order`, `not_unique`), counts failures per column in
`silver/_rule_report/` (`<kind>:<column>`), and is validated by Pandera on each written table, with
its findings in `silver/_contract_report/`. `docs/data-contracts.md` is generated from it.

Late arrivals and volumes only warn, in the job log and two reports; they never fail the run.
Daily tables get `business_date`, the source's business day of the event their process_date follows
(the day starts at 06:00 or 08:00 depending on the source, `BUSINESS_DAY_START`; a digital event
follows its session's first event, transcripts and surveys their interaction), `arrival_lag_days`
(process_date minus business_date) and `is_late_arrival` (more than `LATE_ARRIVAL_DAYS`, 1); a row
processed before its business day is flagged `dq_invalid_processed_before_event`. `silver/_arrival_report/` has the lag distribution per table.
`silver/_volume_report/` compares raw rows with the dictionary's counts (`VOLUME_TOLERANCE_PCT`, 20%) and,
from the `year=/month=/day=` folders, lists days without a file, days with unusual volume (under half or
over twice the median of the same weekday) and rows filed under another day than their process_date.

All reports are Glue tables (`dq_*_report` in the silver database, `dq_exclusions` in gold, from the
`dq_reporting` Terraform module), and the Athena view `dq_summary` puts every check of each table's
latest run in one shape: `layer, report, table_name, check_type, check_name, failing_rows, total_rows,
failing_pct, status, detail, run_at`. For the data quality slide:

```sql
SELECT table_name, check_type, check_name, failing_rows, failing_pct, status, detail
FROM cuy_loyalty_dev_silver.dq_summary
WHERE status NOT IN ('ok')
ORDER BY failing_pct DESC NULLS LAST;
```

Tests build a small bronze lake with one planted case per rule and run both jobs on it:

```bash
uv run --no-project --python 3.11 --with-requirements tests/integration/requirements.txt pytest tests/integration
```

Both scripts run unchanged on AWS Glue 5.0 (Spark 3.5) and locally against a folder:

```bash
pip install -r tests/integration/requirements.txt  # pyspark 3.5.4, pandera and what it needs
python data/pipelines/glue/bronze_to_silver.py --lake_bucket /path/to/lake
python data/pipelines/glue/silver_to_gold.py  --lake_bucket /path/to/lake
```

Locally, without `--gold_db` and `--agent_db`, gold is written as files only; on Glue both databases
are required job arguments, set by Terraform.
