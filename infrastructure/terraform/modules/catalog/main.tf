# Glue Data Catalog databases (one per layer), a crawler that discovers the raw
# bronze files, and an Athena workgroup to query everything with SQL.
resource "aws_glue_catalog_database" "layer" {
  for_each     = toset(["bronze", "silver", "gold"])
  name         = "${replace(var.name_prefix, "-", "_")}_${each.value}"
  location_uri = "s3://${var.lake_bucket}/${each.value}/"
}

resource "aws_glue_crawler" "bronze" {
  name          = "${var.name_prefix}-bronze-crawler"
  role          = var.crawler_role_arn
  database_name = aws_glue_catalog_database.layer["bronze"].name

  # One table per folder under bronze/, e.g. bronze/customers/ -> customers.
  s3_target {
    path = "s3://${var.lake_bucket}/bronze/"
  }

  configuration = jsonencode({
    Version = 1.0
    Grouping = {
      TableLevelConfiguration = 3
    }
  })

  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "LOG"
  }
}

resource "aws_athena_workgroup" "this" {
  name          = var.name_prefix
  force_destroy = true

  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 10737418240 # 10 GB guardrail

    result_configuration {
      output_location = "s3://${var.artifacts_bucket}/athena-results/"
    }
  }
}
