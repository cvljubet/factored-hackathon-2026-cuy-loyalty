# Data Pipelines

Place data ingestion, validation, and transformation pipeline code here.

## Glue jobs (`glue/`)

| Job | Reads | Writes |
|---|---|---|
| `bronze_to_silver.py` | `bronze/<table>/ingest_date=*/` raw CSVs | `silver/<table>/` Parquet, typed, deduplicated on the primary key, quality flags (below); `silver/_dq_report/` row counts, `silver/_rule_report/` failing rows per rule, `silver/_fk_report/` orphans per foreign key |
| `silver_to_gold.py` | silver tables | `gold/product_catalog`, `gold/customer_features`, `gold/customer_360`; `gold/_exclusions/` rows left out per table and reason |

`gold/recommendations` is written by the ranking model, not by these jobs.

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
breaks none. Gold decides which reasons keep a row out (`EXCLUDE_WHEN` in `silver_to_gold.py`) and
counts them.

The rules themselves live in `dq_rules.py`, which Terraform uploads next to the jobs. Today that is the
24 foreign keys from the data dictionary: every child table gets `dq_invalid_orphan_<parent>` per key,
and the silver job fails when a key has more than 5% orphans (warns above 1%). Tables are built parents
first for that reason, so with `--tables` the parents must already be in silver.

Tests build a small bronze lake with one planted case per rule and run both jobs on it:

```bash
uv run --no-project --python 3.11 --with pyspark==3.5.4 --with pytest pytest tests/integration
```

Both scripts run unchanged on AWS Glue 5.0 (Spark 3.5) and locally against a folder:

```bash
pip install pyspark==3.5.4
python data/pipelines/glue/bronze_to_silver.py --lake_bucket /path/to/lake
python data/pipelines/glue/silver_to_gold.py  --lake_bucket /path/to/lake
```
