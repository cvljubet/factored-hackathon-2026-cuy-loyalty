"""AWS and storage access for the engagement-risk pipeline (team account only).

* Silver sources are read with DuckDB straight from the S3 locations the Glue Catalog gives, with column
  projection; an optional local DuckDB file caches them between runs (gitignored data/processed/).
* Model artifacts go to the artifacts bucket under ARTIFACTS_PREFIX.
* Scores go to the gold database's own location (resolved from Glue) as Parquet, partitioned by as_of_date,
  and are registered as a Glue table.
"""

import hashlib
import re
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pandas as pd

from ml.engagement_risk import config


def aws_session(profile: str | None) -> boto3.Session:
    """The named profile when given (local runs), else the standard credential chain. Refuses any account
    other than the team account, so the Bedrock profile can never be used by mistake."""
    session = boto3.Session(profile_name=profile, region_name=config.REGION)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != config.TEAM_ACCOUNT:
        raise SystemExit(f"credentials are for account {account}, not the team account {config.TEAM_ACCOUNT}")
    return session


def glue_location(glue: Any, database: str, table: str) -> str:
    return glue.get_table(DatabaseName=database, Name=table)["Table"]["StorageDescriptor"]["Location"].rstrip("/")


def split_s3(uri: str) -> tuple[str, str]:
    match = re.fullmatch(r"s3a?://([^/]+)/?(.*)", uri)
    if not match:
        raise ValueError(f"not an S3 URI: {uri}")
    return match.group(1), match.group(2)


def connect(profile: str | None, cache_path: Path | None) -> duckdb.DuckDBPyConnection:
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(cache_path) if cache_path else ":memory:")
    con.sql("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
    con.sql("SET enable_progress_bar = false")
    profile_clause = f", PROFILE '{profile}'" if profile else ""
    con.sql(f"CREATE OR REPLACE SECRET lake (TYPE s3, PROVIDER credential_chain{profile_clause}, REGION '{config.REGION}')")
    return con


def _not_excluded(table: str) -> str:
    reasons = ", ".join(f"'{r}'" for r in config.EXCLUDE[table])
    return f"NOT list_has_any(dq_reasons, [{reasons}])"


# DuckDB table -> (silver table, columns, row filter applied at load), as in the notebook's cache.
SOURCES = {
    "src_transactions": ("transactions", "transaction_id, customer_id, transaction_date, business_date, transaction_type, "
                         "transaction_category, merchant_category, transaction_status, amount, currency, amount_usd, dq_reasons", "TRUE"),
    "src_customers": ("customers", "customer_id, registration_date", "TRUE"),
    "src_products": ("products", "customer_id, opening_date", _not_excluded("products")),
    "src_sends": ("campaign_sends", "customer_id, campaign_id, send_date, send_channel, was_delivered, was_opened, open_date, "
                  "was_clicked, click_date", _not_excluded("campaign_sends")),
    "src_contacts": ("call_center_interactions", "customer_id, interaction_date", _not_excluded("call_center_interactions")),
    "src_complaints": ("complaints", "customer_id, creation_date", _not_excluded("complaints")),
}


def load_sources(con: duckdb.DuckDBPyConnection, glue: Any, refresh: bool = False) -> dict[str, int]:
    """Silver -> DuckDB source tables (kept in the cache file when one is used). Returns row counts."""
    def has(name):
        return con.sql(f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{name}'").fetchone()[0] > 0

    for name, (table, cols, where) in SOURCES.items():
        if refresh or not has(name):
            loc = glue_location(glue, config.SILVER_DB, table)
            con.sql(f"CREATE OR REPLACE TABLE {name} AS SELECT {cols} FROM read_parquet('{loc}/**/*.parquet', "
                    f"hive_partitioning = true) WHERE {where}")
    if refresh or not has("src_digital_daily"):
        loc = glue_location(glue, config.SILVER_DB, "digital_events")
        con.sql(f"""CREATE OR REPLACE TABLE src_digital_daily AS
            SELECT customer_id, CAST(event_date AS DATE) AS day, count(*) AS events, count(*) FILTER (WHERE event_type = 'Login') AS logins
            FROM read_parquet('{loc}/**/*.parquet', hive_partitioning = true) WHERE customer_id IS NOT NULL GROUP BY 1, 2""")
    return {n: con.sql(f"SELECT count(*) FROM {n}").fetchone()[0] for n in [*SOURCES, "src_digital_daily"]}


def upload_artifacts(s3: Any, local_dir: Path, overwrite: bool) -> list[str]:
    """Uploads every file in local_dir to s3://ARTIFACTS_BUCKET/ARTIFACTS_PREFIX. An existing model.txt that differs
    from the new one is only replaced with overwrite=True (an identical, reproduced model is fine)."""
    model_key = config.ARTIFACTS_PREFIX + "model.txt"
    try:
        existing = s3.get_object(Bucket=config.ARTIFACTS_BUCKET, Key=model_key)["Body"].read()
    except s3.exceptions.NoSuchKey:
        existing = None
    new = (local_dir / "model.txt").read_bytes()
    if existing is not None and existing != new and not overwrite:
        raise SystemExit(f"s3://{config.ARTIFACTS_BUCKET}/{model_key} holds a different model; rerun with --overwrite "
                         "or bump ARTIFACT_VERSION")
    uris = []
    for path in sorted(local_dir.iterdir()):
        if path.is_file():
            key = config.ARTIFACTS_PREFIX + path.name
            s3.upload_file(str(path), config.ARTIFACTS_BUCKET, key)
            uris.append(f"s3://{config.ARTIFACTS_BUCKET}/{key}")
    return uris


def download_artifacts(s3: Any, local_dir: Path, names=("model.txt", "metadata.json", "feature_schema.json")) -> Path:
    local_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        s3.download_file(config.ARTIFACTS_BUCKET, config.ARTIFACTS_PREFIX + name, str(local_dir / name))
    return local_dir


GOLD_COLUMNS = [  # Parquet columns; as_of_date is the partition key
    ("customer_id", "string"), ("model_version", "string"), ("model_eligible", "boolean"), ("scoring_source", "string"),
    ("risk_score", "double"), ("risk_tier", "string"), ("reason_code", "string"),
]


def write_parquet(scores: pd.DataFrame, path: Path) -> None:
    """The serving rows as Parquet with explicit types (risk_score stays NULL for fallback customers)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.register("scores", scores)
    con.sql(f"""COPY (SELECT customer_id::VARCHAR AS customer_id, model_version::VARCHAR AS model_version,
                             model_eligible::BOOLEAN AS model_eligible, scoring_source::VARCHAR AS scoring_source,
                             CASE WHEN model_eligible THEN risk_score::DOUBLE END AS risk_score, risk_tier::VARCHAR AS risk_tier, reason_code::VARCHAR AS reason_code
                      FROM scores ORDER BY customer_id)
                TO '{path.as_posix()}' (FORMAT PARQUET)""")
    rows, null_scores = con.sql(f"SELECT count(*), count(*) FILTER (WHERE risk_score IS NULL) FROM '{path.as_posix()}'").fetchone()
    con.close()
    if rows != len(scores) or null_scores != int((~scores.model_eligible.astype(bool)).sum()):
        raise ValueError(f"{path}: {rows} rows / {null_scores} NULL scores do not match the scores frame")


def gold_table_location(glue: Any) -> str:
    gold_root = glue.get_database(Name=config.GOLD_DB)["Database"]["LocationUri"].rstrip("/")
    return f"{gold_root}/{config.GOLD_TABLE}"


def publish_gold(session: boto3.Session, parquet: Path, as_of: str) -> dict[str, str]:
    """Uploads the partition file (same key on every rerun, so a rerun replaces it) and registers the Glue table
    cuy_loyalty_dev_gold.engagement_risk_scores with partition as_of_date."""
    glue, s3 = session.client("glue"), session.client("s3")
    table_loc = gold_table_location(glue)
    part_loc = f"{table_loc}/as_of_date={as_of}"
    bucket, prefix = split_s3(part_loc)
    key = f"{prefix}/part-00000.parquet"
    s3.upload_file(str(parquet), bucket, key)

    serde = {"SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
             "Parameters": {"serialization.format": "1"}}
    sd = {"Columns": [{"Name": n, "Type": t} for n, t in GOLD_COLUMNS], "Location": table_loc,
          "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
          "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat", "SerdeInfo": serde}
    table_input = {"Name": config.GOLD_TABLE, "TableType": "EXTERNAL_TABLE",
                   "Description": f"Loyalty engagement-risk scores ({config.MODEL_NAME}); one row per customer per as_of_date.",
                   "Parameters": {"classification": "parquet", "EXTERNAL": "TRUE"},
                   "PartitionKeys": [{"Name": "as_of_date", "Type": "string"}], "StorageDescriptor": sd}
    try:
        glue.get_table(DatabaseName=config.GOLD_DB, Name=config.GOLD_TABLE)
        glue.update_table(DatabaseName=config.GOLD_DB, TableInput=table_input)
    except glue.exceptions.EntityNotFoundException:
        glue.create_table(DatabaseName=config.GOLD_DB, TableInput=table_input)
    partition = {"Values": [as_of], "StorageDescriptor": {**sd, "Location": part_loc}}
    try:
        glue.get_partition(DatabaseName=config.GOLD_DB, TableName=config.GOLD_TABLE, PartitionValues=[as_of])
        glue.update_partition(DatabaseName=config.GOLD_DB, TableName=config.GOLD_TABLE, PartitionValueList=[as_of],
                              PartitionInput=partition)
    except glue.exceptions.EntityNotFoundException:
        glue.create_partition(DatabaseName=config.GOLD_DB, TableName=config.GOLD_TABLE, PartitionInput=partition)
    return {"table": f"{config.GOLD_DB}.{config.GOLD_TABLE}", "location": table_loc, "partition": part_loc,
            "object": f"s3://{bucket}/{key}"}


def pseudonym(customer_id: str) -> str:
    """A short stable hash for printing example rows without customer ids."""
    return hashlib.sha256(customer_id.encode()).hexdigest()[:10]
