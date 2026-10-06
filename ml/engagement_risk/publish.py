"""Publish one as-of date of Gold engagement-risk scores to the DynamoDB customer-serving table.

    python -m ml.engagement_risk.publish --profile cuy-loyalty --as-of 2026-06-17 --dry-run
    python -m ml.engagement_risk.publish --profile cuy-loyalty --as-of 2026-06-17

One item per customer: PK = CUST#<customer_id>, SK = ENGAGEMENT_RISK, with model_version, as_of_date,
model_eligible, scoring_source, risk_score (only when the model scored the customer), risk_tier and
reason_code (only for fallbacks). customer_id lives in PK only and no model feature is stored.

The publisher only ever puts SK = ENGAGEMENT_RISK items. Rerunning a date overwrites the same keys. It never
deletes or updates anything, so PROFILE, TXN#, CONTACT#, COMPLAINT#, CAMPAIGN#, branch and FX items are never
touched. After writing, every item is read back with BatchGetItem and compared with its Gold row.
"""

import argparse
import math
import tempfile
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from ml.engagement_risk import config, score

SERVING_TABLE = "cuy-loyalty-dev-customer-serving"
SK = "ENGAGEMENT_RISK"
ATTRIBUTES = ("model_version", "as_of_date", "model_eligible", "scoring_source", "risk_score", "risk_tier", "reason_code")


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA


def build_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """The DynamoDB item for one Gold row. A NULL score or reason is left out, never invented."""
    cid = row.get("customer_id")
    if _missing(cid) or not str(cid).strip() or "#" in str(cid):
        raise ValueError(f"unusable customer_id {cid!r}")
    eligible = bool(row["model_eligible"])
    item: dict[str, Any] = {
        "PK": f"CUST#{cid}", "SK": SK,
        "model_version": str(row["model_version"]),
        "as_of_date": str(pd.Timestamp(row["as_of_date"]).date()),
        "model_eligible": eligible,
        "scoring_source": str(row["scoring_source"]),
        "risk_tier": str(row["risk_tier"]),
    }
    if not _missing(row.get("risk_score")):
        if not eligible:
            raise ValueError(f"{cid}: fallback row with a risk_score")
        item["risk_score"] = Decimal(repr(float(row["risk_score"])))
    elif eligible:
        raise ValueError(f"{cid}: model row without a risk_score")
    if not _missing(row.get("reason_code")):
        item["reason_code"] = str(row["reason_code"])
    return item


def validate_gold(rows: pd.DataFrame, as_of: date) -> None:
    """The Gold rows are one per customer for this date and model, with the serving invariants."""
    frame = rows.assign(as_of_date=as_of)[score.OUTPUT_COLUMNS]
    score.validate_scores(frame)  # unique ids, valid model scores, NULL fallback scores, tier / reason consistency
    if set(rows.model_version) != {config.MODEL_VERSION}:
        raise ValueError(f"unexpected model_version values {set(rows.model_version)}")


def read_gold(session: Any, as_of: date) -> pd.DataFrame:
    """The Gold partition for as_of, located through the Glue Catalog."""
    import duckdb

    from ml.engagement_risk import io

    glue, s3 = session.client("glue"), session.client("s3")
    part = glue.get_partition(DatabaseName=config.GOLD_DB, TableName=config.GOLD_TABLE, PartitionValues=[str(as_of)])
    bucket, prefix = io.split_s3(part["Partition"]["StorageDescriptor"]["Location"])
    keys = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/")
            for o in page.get("Contents", []) if o["Key"].endswith(".parquet")]
    if not keys:
        raise SystemExit(f"no Parquet files in s3://{bucket}/{prefix}")
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, key in enumerate(sorted(keys)):
            paths.append(Path(tmp) / f"part-{i}.parquet")
            s3.download_file(bucket, key, str(paths[-1]))
        files = ", ".join(f"'{p.as_posix()}'" for p in paths)
        rows = duckdb.sql(f"SELECT * FROM read_parquet([{files}]) ORDER BY customer_id").df()
    rows["as_of_date"] = as_of
    return rows


def _chunks(items: list, n: int) -> list[list]:
    size = math.ceil(len(items) / n) if items else 0
    return [items[i:i + size] for i in range(0, len(items), size)] if size else []


def write_items(table_factory, items: list[dict[str, Any]], workers: int) -> int:
    """Puts the items with one batch_writer per worker thread (25 per request, unprocessed items resent)."""
    if any(i["SK"] != SK for i in items):
        raise ValueError(f"the publisher only writes SK = {SK}")

    def put(chunk):
        table = table_factory()  # one boto3 resource per thread
        with table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as batch:
            for item in chunk:
                batch.put_item(Item=item)
        return len(chunk)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sum(pool.map(put, _chunks(items, workers)))


def read_back(client_factory, table_name: str, keys: list[dict[str, str]], workers: int) -> dict[str, dict]:
    """BatchGetItem for the given keys (100 per request, unprocessed keys retried). Returns items by PK."""
    from boto3.dynamodb.types import TypeDeserializer

    des = TypeDeserializer()

    def get(chunk):
        client, found = client_factory(), {}
        for i in range(0, len(chunk), 100):
            request = {table_name: {"Keys": [{"PK": {"S": k["PK"]}, "SK": {"S": k["SK"]}} for k in chunk[i:i + 100]],
                                    "ConsistentRead": True}}
            while request:
                resp = client.batch_get_item(RequestItems=request)
                for raw in resp["Responses"].get(table_name, []):
                    item = {k: des.deserialize(v) for k, v in raw.items()}
                    found[item["PK"]] = item
                request = resp.get("UnprocessedKeys") or None
        return found

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(get, _chunks(keys, workers)):
            out.update(part)
    return out


def compare(expected: Iterable[dict[str, Any]], found: Mapping[str, dict]) -> list[str]:
    """Differences between the items that should exist and what DynamoDB returned (empty = identical)."""
    problems = []
    for item in expected:
        got = found.get(item["PK"])
        if got is None:
            problems.append(f"{item['PK']}: missing")
        elif got != item:
            problems.append(f"{item['PK']}: {sorted(set(got.items()) ^ set(item.items()))}")
    return problems


def counts(items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    df = pd.DataFrame(list(items))
    model = df[df.model_eligible]
    return {"items": len(df), "model": int(len(model)), "fallback": int((~df.model_eligible).sum()),
            "model_tiers": model.risk_tier.value_counts().to_dict(),
            "fallback_reasons": df[~df.model_eligible].reason_code.value_counts().to_dict(),
            "fallback_tiers": df[~df.model_eligible].risk_tier.value_counts().to_dict()}


def main(argv=None) -> None:
    import json

    from ml.engagement_risk import io

    p = argparse.ArgumentParser(description="Publish Gold engagement-risk scores to the DynamoDB serving table.")
    p.add_argument("--profile", default=config.DEFAULT_PROFILE, help="AWS profile; '' uses the default chain.")
    p.add_argument("--as-of", type=date.fromisoformat, default=config.SERVING_AS_OF)
    p.add_argument("--table", default=SERVING_TABLE)
    p.add_argument("--dry-run", action="store_true", help="Read Gold, build and validate the items; write nothing.")
    p.add_argument("--limit", type=int, help="Publish only the first N customers (by customer_id).")
    p.add_argument("--customer-id", action="append", default=[], help="Publish only these customers; repeatable.")
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args(argv)

    session = io.aws_session(args.profile or None)  # refuses any account but the team account
    rows = read_gold(session, args.as_of)
    validate_gold(rows, args.as_of)
    if args.customer_id:
        missing = set(args.customer_id) - set(rows.customer_id)
        if missing:
            raise SystemExit(f"not in Gold for {args.as_of}: {sorted(missing)}")
        rows = rows[rows.customer_id.isin(args.customer_id)]
    if args.limit:
        rows = rows.head(args.limit)
    items = [build_item(r) for r in rows.to_dict("records")]
    summary = {"as_of_date": str(args.as_of), "gold_rows_selected": len(rows), **counts(items)}

    if not args.dry_run:
        def table_factory():
            return io.aws_session(args.profile or None).resource("dynamodb").Table(args.table)

        def client_factory():
            return io.aws_session(args.profile or None).client("dynamodb")

        summary["written"] = write_items(table_factory, items, args.workers)
        found = read_back(client_factory, args.table, [{"PK": i["PK"], "SK": SK} for i in items], args.workers)
        problems = compare(items, found)
        summary["read_back"] = {"found": len(found), "identical": len(items) - len(problems), "problems": problems[:10]}
        if problems:
            print(json.dumps(summary, indent=2, default=str))
            raise SystemExit(f"{len(problems)} items do not match Gold after publishing")
    summary["examples"] = {kind: next((i for i in items if pred(i)), None) for kind, pred in (
        ("model", lambda i: i["model_eligible"]), ("fallback", lambda i: not i["model_eligible"]))}
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
