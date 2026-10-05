# Step 4 of the guide: raw prompts and responses in CloudWatch Logs (the model account's own record).
resource "aws_cloudwatch_log_group" "invocations" {
  count             = var.enable_invocation_logging ? 1 : 0
  name              = "/bedrock/${var.name_prefix}"
  retention_in_days = 14
}

resource "aws_iam_role" "invocation_logging" {
  count = var.enable_invocation_logging ? 1 : 0
  name  = "${var.name_prefix}-bedrock-logging"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "bedrock.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_iam_role_policy" "invocation_logging" {
  count = var.enable_invocation_logging ? 1 : 0
  name  = "write-logs"
  role  = aws_iam_role.invocation_logging[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.invocations[0].arn}:*"
    }]
  })
}

resource "aws_bedrock_model_invocation_logging_configuration" "this" {
  count      = var.enable_invocation_logging ? 1 : 0
  depends_on = [aws_iam_role_policy.invocation_logging]

  logging_config {
    text_data_delivery_enabled      = true
    embedding_data_delivery_enabled = false
    image_data_delivery_enabled     = false
    video_data_delivery_enabled     = false

    cloudwatch_config {
      log_group_name = aws_cloudwatch_log_group.invocations[0].name
      role_arn       = aws_iam_role.invocation_logging[0].arn
    }
  }
}
