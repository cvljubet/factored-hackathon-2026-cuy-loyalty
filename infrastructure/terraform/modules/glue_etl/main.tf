# Uploads the PySpark scripts and defines the bronze->silver and silver->gold
# jobs, chained in a Glue workflow: build silver, then build gold.
# The silver job reads bronze CSVs straight from S3, so the bronze crawler is
# only for browsing raw data in Athena and runs on its own.
locals {
  scripts = {
    bronze_to_silver = "${var.scripts_dir}/bronze_to_silver.py"
    silver_to_gold   = "${var.scripts_dir}/silver_to_gold.py"
  }

  common_args = {
    "--lake_bucket"                      = var.lake_bucket
    "--bronze_db"                        = var.database_names["bronze"]
    "--silver_db"                        = var.database_names["silver"]
    "--gold_db"                          = var.database_names["gold"]
    "--agent_db"                         = var.database_names["agent"]
    "--enable-glue-datacatalog"          = "true"
    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-spark-ui"                  = "true"
    "--spark-event-logs-path"            = "s3://${var.artifacts_bucket}/spark-logs/"
    "--TempDir"                          = "s3://${var.artifacts_bucket}/tmp/"
    "--job-language"                     = "python"
    "--extra-py-files" = join(",", [
      "s3://${var.artifacts_bucket}/${aws_s3_object.dq_rules.key}",
      "s3://${var.artifacts_bucket}/${aws_s3_object.table_rules.key}",
      "s3://${var.artifacts_bucket}/${aws_s3_object.contracts.key}",
    ])
    # Plain pandera, not pandera[pyspark]: that extra would install its own pyspark over Glue's.
    "--additional-python-modules" = "pandera==0.33.1"
  }
}

resource "aws_s3_object" "script" {
  for_each    = local.scripts
  bucket      = var.artifacts_bucket
  key         = "glue-scripts/${each.key}.py"
  source      = each.value
  source_hash = filemd5(each.value)
}

# Rules modules the jobs import; --extra-py-files puts them on their Python path.
resource "aws_s3_object" "dq_rules" {
  bucket      = var.artifacts_bucket
  key         = "glue-scripts/dq_rules.py"
  source      = "${var.scripts_dir}/dq_rules.py"
  source_hash = filemd5("${var.scripts_dir}/dq_rules.py")
}

resource "aws_s3_object" "table_rules" {
  bucket      = var.artifacts_bucket
  key         = "glue-scripts/table_rules.py"
  source      = "${var.scripts_dir}/table_rules.py"
  source_hash = filemd5("${var.scripts_dir}/table_rules.py")
}

resource "aws_s3_object" "contracts" {
  bucket      = var.artifacts_bucket
  key         = "glue-scripts/contracts.py"
  source      = "${var.scripts_dir}/contracts.py"
  source_hash = filemd5("${var.scripts_dir}/contracts.py")
}

resource "aws_glue_job" "this" {
  for_each = local.scripts

  name              = "${var.name_prefix}-${replace(each.key, "_", "-")}"
  role_arn          = var.glue_role_arn
  glue_version      = "5.0"
  worker_type       = var.worker_type
  number_of_workers = var.number_of_workers
  timeout           = 60
  max_retries       = 0

  command {
    name            = "glueetl"
    script_location = "s3://${var.artifacts_bucket}/${aws_s3_object.script[each.key].key}"
    python_version  = "3"
  }

  default_arguments = local.common_args

  execution_property {
    max_concurrent_runs = 1
  }
}

resource "aws_glue_workflow" "medallion" {
  name = "${var.name_prefix}-medallion"
}

resource "aws_glue_trigger" "start" {
  name          = "${var.name_prefix}-start"
  type          = "ON_DEMAND"
  workflow_name = aws_glue_workflow.medallion.name

  actions {
    job_name = aws_glue_job.this["bronze_to_silver"].name
  }
}

resource "aws_glue_trigger" "after_silver" {
  name          = "${var.name_prefix}-after-silver"
  type          = "CONDITIONAL"
  workflow_name = aws_glue_workflow.medallion.name

  predicate {
    conditions {
      job_name = aws_glue_job.this["bronze_to_silver"].name
      state    = "SUCCEEDED"
    }
  }

  actions {
    job_name = aws_glue_job.this["silver_to_gold"].name
  }
}
