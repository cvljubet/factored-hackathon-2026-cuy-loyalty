# Glue Data Catalog databases (one per layer, plus the agent zone of gold), a crawler that
# discovers the raw bronze files, an Athena workgroup to query everything with SQL, and the
# read-only policy for whatever serves the assistant.
locals {
  # database key -> S3 prefix. agent is the part of gold the assistant may read.
  databases = {
    bronze = "bronze"
    silver = "silver"
    gold   = "gold"
    agent  = "gold/agent"
  }
}

resource "aws_glue_catalog_database" "layer" {
  for_each     = local.databases
  name         = "${replace(var.name_prefix, "-", "_")}_${each.key}"
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

# The assistant's only access to the lake: the agent zone's files and catalog entries. Not
# attached here; the backend's task role attaches it when it is created. customer_features and
# the rest of gold stay out of reach, so no tool bug can return credit_score or income.
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  glue_arn = "arn:aws:glue:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}"
}

data "aws_iam_policy_document" "agent_read" {
  statement {
    sid       = "ListAgentZone"
    actions   = ["s3:ListBucket"]
    resources = ["arn:aws:s3:::${var.lake_bucket}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["gold/agent/*"]
    }
  }

  statement {
    sid       = "ReadAgentZone"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.lake_bucket}/gold/agent/*"]
  }

  statement {
    sid     = "ReadAgentCatalog"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartition", "glue:GetPartitions"]
    resources = [
      "${local.glue_arn}:catalog",
      aws_glue_catalog_database.layer["agent"].arn,
      "${local.glue_arn}:table/${aws_glue_catalog_database.layer["agent"].name}/*",
    ]
  }
}

resource "aws_iam_policy" "agent_read" {
  name        = "${var.name_prefix}-agent-zone-read"
  description = "Read-only access to gold/agent/ and its Glue database, for the assistant."
  policy      = data.aws_iam_policy_document.agent_read.json
}
