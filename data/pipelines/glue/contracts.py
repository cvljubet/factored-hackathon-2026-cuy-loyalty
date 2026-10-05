"""Data contracts: one Pandera schema per silver table, built from the dictionary's constraints
(NOT NULL, UNIQUE) and the rules in dq_rules (allowed values, ranges, event dates, date order),
with the column types silver writes.

Each schema is used three ways:
  - validate(): Pandera checks a written silver table; its findings go to silver/_contract_report/.
    Pandera's PySpark backend says whether each check passed, not how many rows failed.
  - add_flags(): the same checks compiled to Spark expressions give every row its dq_invalid_<kind>
    flags (required_missing, not_allowed, out_of_range, outside_dataset, date_order, not_unique).
  - counts(): rows failing each check, per column, for silver/_rule_report/.
render_markdown() turns them into docs/data-contracts.md:
    python data/pipelines/glue/contracts.py > docs/data-contracts.md

Deployed next to the job scripts and put on their Python path with --extra-py-files; Glue
installs pandera from --additional-python-modules.
"""
import datetime
import sys
from functools import reduce

import pandera.pyspark as pa
from pyspark.sql import Column, DataFrame, Row, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

from dq_rules import (
    ALLOWED_VALUES,
    BUSINESS_DAY_START,
    DATASET_END,
    DATASET_START,
    DATE_ORDER,
    DROPPED_COLUMNS,
    EVENT_DATES,
    FOREIGN_KEYS,
    RANGES,
    REQUIRED,
    UNIQUE,
)

SILVER_TYPES = {
    "dates": T.DateType(),
    "timestamps": T.TimestampType(),
    "ints": T.IntegerType(),
    "decimals": T.DecimalType(20, 8),
    "doubles": T.DoubleType(),
    "bools": T.BooleanType(),
}
KINDS = ["required_missing", "not_allowed", "out_of_range", "outside_dataset", "date_order", "not_unique"]
CONTRACT_REPORT = "table string, category string, error_type string, column string, check string, error string"


def seconds_of_day(ts: Column) -> Column:
    return F.hour(ts) * 3600 + F.minute(ts) * 60 + F.second(ts)


def business_day(ts: Column, starts_at: str) -> Column:
    """The source's business day of a timestamp: before starts_at ("HH:MM:SS") it is the previous day."""
    h, m, s = map(int, starts_at.split(":"))
    return F.when(seconds_of_day(ts) < h * 3600 + m * 60 + s, F.date_sub(F.to_date(ts), 1)).otherwise(F.to_date(ts))


def within_dataset(table: str) -> pa.Check:
    """The event's business day (see dq_rules.BUSINESS_DAY_START) is between DATASET_START and
    DATASET_END: the last business day runs into the next morning."""
    start, end = datetime.date.fromisoformat(DATASET_START), datetime.date.fromisoformat(DATASET_END)
    starts_at = BUSINESS_DAY_START.get(table, "00:00:00")

    def check(data, start, end, starts_at) -> bool:
        day = business_day(F.col(data.column_name), starts_at)
        return data.dataframe.filter(~day.between(start, end)).limit(1).count() == 0

    # Pandera's PySpark backend hands a custom column check its column only when the check has
    # keyword arguments (start, end, starts_at here); without them it gets the bare DataFrame.
    return pa.Check(
        check, name="within_dataset", error=f"within_dataset({DATASET_START}, {DATASET_END})",
        statistics={"start": start, "end": end, "starts_at": starts_at}, start=start, end=end, starts_at=starts_at,
    )


def date_order(earlier: str, later: str) -> pa.Check:
    def check(df: DataFrame) -> bool:  # dataframe-level checks get the DataFrame itself
        return df.filter(F.col(earlier) > F.col(later)).limit(1).count() == 0

    return pa.Check(
        check, name="date_order", error=f"{earlier} <= {later}", statistics={"earlier": earlier, "later": later}
    )


def unique_values(column: str) -> pa.Check:
    def check(df: DataFrame) -> bool:
        repeated = df.where(F.col(column).isNotNull()).groupBy(column).count().where("count > 1")
        return repeated.limit(1).count() == 0

    return pa.Check(check, name="unique_values", error=f"unique({column})", statistics={"column": column})


def build(table: str, cfg: dict, columns: list) -> pa.DataFrameSchema:
    """The contract for one silver table: its dictionary columns (minus DROPPED_COLUMNS) with the
    types silver gives them; columns silver adds (flags, imputations, derived) aren't part of it."""
    types = {c: dtype for kind, dtype in SILVER_TYPES.items() for c in cfg.get(kind, [])}
    schema_columns = {}
    for c in columns:
        if c in DROPPED_COLUMNS.get(table, []):
            continue
        checks = []
        if c in ALLOWED_VALUES.get(table, {}):
            checks.append(pa.Check.isin(ALLOWED_VALUES[table][c]))
        if c in RANGES.get(table, {}):
            low, high = RANGES[table][c]
            checks.append(pa.Check.ge(low) if high is None else pa.Check.in_range(low, high))
        if EVENT_DATES.get(table) == c:
            checks.append(within_dataset(table))
        nullable = c not in REQUIRED.get(table, [])
        schema_columns[c] = pa.Column(types.get(c, T.StringType()), checks=checks, nullable=nullable)
    table_checks = [date_order(a, b) for a, b in DATE_ORDER.get(table, [])]
    table_checks += [unique_values(c) for c in UNIQUE.get(table, [])]
    return pa.DataFrameSchema(schema_columns, checks=table_checks, unique=cfg["pk"], name=table)


def build_all(tables: dict, columns: dict) -> dict:
    return {t: build(t, cfg, columns[t]) for t, cfg in tables.items()}


def failing(schema: pa.DataFrameSchema) -> list:
    """(kind, column, condition true on a failing row) for every check, compiled from the schema.
    A null input fails no check except required_missing. Primary and foreign key columns get no
    required_missing: missing_key and the orphan flags already cover them."""
    keys = set(schema.unique or []) | {fk.column for fk in FOREIGN_KEYS if fk.child == schema.name}
    out = []
    for name, column in schema.columns.items():
        value = F.col(name)
        if not column.nullable and name not in keys:
            out.append(("required_missing", name, value.isNull()))
        for check in column.checks:
            stats = check.statistics
            if check.name == "isin":
                out.append(("not_allowed", name, ~value.isin(*stats["allowed_values"])))
            elif check.name == "in_range":
                out.append(("out_of_range", name, ~value.between(stats["min_value"], stats["max_value"])))
            elif check.name == "greater_than_or_equal_to":
                out.append(("out_of_range", name, value < stats["min_value"]))
            elif check.name == "within_dataset":
                day = business_day(value, stats["starts_at"])
                out.append(("outside_dataset", name, ~day.between(stats["start"], stats["end"])))
            else:
                raise ValueError(f"{schema.name}.{name}: no row-level rule for check {check.name}")
    for check in schema.checks:
        stats = check.statistics
        if check.name == "date_order":
            out.append(("date_order", stats["later"], F.col(stats["earlier"]) > F.col(stats["later"])))
        elif check.name == "unique_values":
            column = F.col(stats["column"])
            repeated = F.count("*").over(Window.partitionBy(stats["column"])) > 1
            out.append(("not_unique", stats["column"], column.isNotNull() & repeated))
        else:
            raise ValueError(f"{schema.name}: no row-level rule for check {check.name}")
    return out


def add_flags(df: DataFrame, schema: pa.DataFrameSchema) -> DataFrame:
    """One dq_invalid_<kind> flag per kind of check the table has: true when any column fails it."""
    by_kind = {}
    for kind, _, condition in failing(schema):
        by_kind.setdefault(kind, []).append(condition)
    flags = {f"dq_invalid_{kind}": reduce(lambda a, b: a | b, conds) for kind, conds in by_kind.items()}
    return df.withColumns(flags) if flags else df


def counts(df: DataFrame, schema: pa.DataFrameSchema) -> dict:
    """{"<kind>:<column>": rows failing that check}, in one pass over the table."""
    checks = failing(schema)
    if not checks:
        return {}
    marked = df.select(*[F.when(cond, 1).alias(f"{kind}:{column}") for kind, column, cond in checks])
    return marked.agg(*[F.count(F.col(f"`{c}`")).alias(c) for c in marked.columns]).first().asDict()


def validate(df: DataFrame, schema: pa.DataFrameSchema) -> list:
    """Pandera's findings on a written table, one row per failed check (empty when all pass)."""
    errors = schema.validate(df).pandera.errors
    return [
        Row(table=schema.name, category=category, error_type=error_type, column=e.get("column"),
            check=e.get("check"), error=short_error(e))
        for category, by_type in errors.items()
        for error_type, found in by_type.items()
        for e in found
    ]


def short_error(error: dict):
    """Pandera's message, without the printout of the whole schema it gives for table-level checks."""
    message = error.get("error")
    if message and message.startswith("<Schema"):
        return f"failed validation {error.get('check')}"
    return message


def render_markdown(contracts: dict) -> str:
    def describe(check: pa.Check) -> str:
        s = check.statistics
        if check.name == "isin":
            return "one of " + ", ".join(map(str, s["allowed_values"]))
        if check.name == "in_range":
            return f"{s['min_value']} to {s['max_value']}"
        if check.name == "greater_than_or_equal_to":
            return f">= {s['min_value']}"
        if check.name == "within_dataset":
            day = f", business day from {s['starts_at']}" if s["starts_at"] != "00:00:00" else ""
            return f"between {s['start']} and {s['end']}{day}"
        return check.error

    out = [
        "# Data contracts",
        "",
        "Generated from `data/pipelines/glue/contracts.py` (Pandera schemas built from the data dictionary and",
        "`dq_rules.py`); regenerate with `python data/pipelines/glue/contracts.py > docs/data-contracts.md`.",
        "",
        "Every silver table is validated against its contract on each run (`silver/_contract_report/`), and",
        "rows breaking it get `dq_invalid_<kind>` flags. Types are the ones silver writes. Silver also adds",
        "columns that aren't part of the contract: `dq_*` flags, `<column>_imputed`, derived columns.",
    ]
    for table, schema in contracts.items():
        out += ["", f"## {table}", "", f"Primary key: `{', '.join(schema.unique or [])}`."]
        extra = [c.error for c in schema.checks]
        if extra:
            out.append("Table checks: " + "; ".join(f"`{e}`" for e in extra) + ".")
        out += ["", "| Column | Type | Required | Rule |", "|---|---|---|---|"]
        for name, column in schema.columns.items():
            rules = "; ".join(describe(c) for c in column.checks)
            out.append(f"| `{name}` | {column.dtype} | {'' if column.nullable else 'yes'} | {rules} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    from bronze_to_silver import COLUMNS, TABLES

    sys.stdout.write(render_markdown(build_all(TABLES, COLUMNS)))
