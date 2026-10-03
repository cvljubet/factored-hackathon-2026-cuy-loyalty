# Data Pipelines

Place data ingestion, validation, and transformation pipeline code here.

## Glue jobs (`glue/`)

| Job | Reads | Writes |
|---|---|---|
| `bronze_to_silver.py` | `bronze/<table>/ingest_date=*/` raw CSVs | `silver/<table>/` Parquet, typed, deduplicated on the primary key, `dq_*` flags, `silver/_dq_report/` row counts |
| `silver_to_gold.py` | silver tables | `gold/product_catalog`, `gold/customer_features`, `gold/customer_360` |

`gold/recommendations` is written by the ranking model, not by these jobs.

Both scripts run unchanged on AWS Glue 5.0 (Spark 3.5) and locally against a folder:

```bash
pip install pyspark==3.5.4
python data/pipelines/glue/bronze_to_silver.py --lake_bucket /path/to/lake
python data/pipelines/glue/silver_to_gold.py  --lake_bucket /path/to/lake
```
