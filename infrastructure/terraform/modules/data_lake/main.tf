# One lake bucket with bronze/, silver/ and gold/ prefixes, plus an artifacts
# bucket for Glue scripts, Glue temp files and Athena query results.
data "aws_caller_identity" "current" {}

locals {
  suffix = "${var.name_prefix}-${data.aws_caller_identity.current.account_id}"
  buckets = {
    lake      = "${local.suffix}-lake"
    artifacts = "${local.suffix}-artifacts"
  }
}

resource "aws_s3_bucket" "this" {
  for_each      = local.buckets
  bucket        = each.value
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = aws_s3_bucket.this
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = aws_s3_bucket.this
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.this["lake"].id
  versioning_configuration {
    status = "Enabled"
  }
}

# Placeholder objects so the layer prefixes show up in the console.
resource "aws_s3_object" "layer_prefix" {
  for_each = toset(["bronze/", "silver/", "gold/"])
  bucket   = aws_s3_bucket.this["lake"].id
  key      = each.value
  content  = ""
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.this["artifacts"].id

  rule {
    id     = "expire-temp-and-query-results"
    status = "Enabled"
    filter {
      prefix = "tmp/"
    }
    expiration {
      days = 7
    }
  }

  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter {
      prefix = "athena-results/"
    }
    expiration {
      days = 7
    }
  }
}
