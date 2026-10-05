import sys
from pathlib import Path

import pytest
from pyspark.sql import SparkSession

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data" / "pipelines" / "glue"))

import bronze_to_silver  # noqa: E402
import silver_to_gold  # noqa: E402
from lake_fixture import write_bronze  # noqa: E402


@pytest.fixture(scope="session")
def spark():
    session = (
        SparkSession.builder.master("local[2]")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.fixture(scope="session")
def lake(spark, tmp_path_factory):
    """The bronze fixture run through both jobs once. Each planted orphan is a double-digit
    % of these tiny tables, so the fail threshold is lifted here; test_job_fails_above_threshold
    runs with the real one."""
    root = tmp_path_factory.mktemp("lake")
    write_bronze(root)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bronze_to_silver, "ORPHAN_FAIL_PCT", 100.0)
        bronze_to_silver.main(["bronze_to_silver.py", "--lake_bucket", str(root)])
    silver_to_gold.main(["silver_to_gold.py", "--lake_bucket", str(root)])
    return root
