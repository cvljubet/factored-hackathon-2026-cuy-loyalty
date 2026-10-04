"""Schema enforcement: every bronze file's header must be COLUMNS[table], same names, same order.

Run with: uv run --no-project --python 3.11 --with pyspark==3.5.4 --with pytest pytest tests/integration
"""
import shutil

import pytest

import bronze_to_silver
from bronze_to_silver import COLUMNS, TABLES
from lake_fixture import ROWS, bronze_file, write_csv

GOOD = COLUMNS["transactions"]
BAD_HEADERS = {
    "reordered": [GOOD[1], GOOD[0], *GOOD[2:]],
    "missing": GOOD[:-1],
    "extra": [*GOOD, "new_column"],
    "renamed": ["txn_id", *GOOD[1:]],
}


def test_columns_cover_every_table():
    assert sorted(COLUMNS) == sorted(TABLES)


def test_silver_keeps_every_dictionary_column(spark, lake):
    for table, columns in COLUMNS.items():
        silver = spark.read.parquet(f"{lake}/silver/{table}/").columns
        assert [c for c in silver if c in columns] == columns, table
        assert "ingest_date" in silver, table


@pytest.mark.parametrize("case", BAD_HEADERS)
def test_a_file_with_another_header_fails_the_job(lake, tmp_path, case):
    copy = tmp_path / "lake"
    shutil.copytree(lake, copy)
    # A later ingest of transactions arrives with a different header, next to the good one.
    bad = bronze_file(copy, "transactions", ingest_date="2026-10-02")
    write_csv(bad, BAD_HEADERS[case], [{c: r.get(c, "") for c in BAD_HEADERS[case]} for r in ROWS["transactions"]])
    # Spark words it differently when only the names differ and when the column count differs.
    with pytest.raises(Exception, match="does not conform to the schema|not equal to number of fields") as err:
        bronze_to_silver.main(["bronze_to_silver.py", "--lake_bucket", str(copy), "--tables", "transactions"])
    assert bad.name in str(err.value)
