# One role shared by the crawler and the ETL jobs. Least privilege per layer:
# bronze is read-only for jobs, silver and gold are read-write.
data "aws_iam_policy_document" "glue_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  name               = "${var.name_prefix}-glue-role"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue_s3" {
  statement {
    sid       = "ListBuckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.lake_bucket_arn, var.artifacts_bucket_arn]
  }

  statement {
    sid       = "ReadBronze"
    actions   = ["s3:GetObject"]
    resources = ["${var.lake_bucket_arn}/bronze/*"]
  }

  statement {
    sid       = "ReadWriteSilverGold"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.lake_bucket_arn}/silver/*", "${var.lake_bucket_arn}/gold/*"]
  }

  statement {
    sid       = "ReadScripts"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/glue-scripts/*"]
  }

  statement {
    sid       = "ReadWriteTemp"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.artifacts_bucket_arn}/tmp/*", "${var.artifacts_bucket_arn}/spark-logs/*"]
  }
}

resource "aws_iam_role_policy" "glue_s3" {
  name   = "lake-access"
  role   = aws_iam_role.glue.id
  policy = data.aws_iam_policy_document.glue_s3.json
}
