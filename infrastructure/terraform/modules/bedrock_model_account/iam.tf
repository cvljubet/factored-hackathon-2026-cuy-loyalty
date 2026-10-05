# Role the team account assumes to call Bedrock (step 5 of bedrock-second-account-guide.md).
resource "aws_iam_role" "invoker" {
  name = var.invoker_role_name

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.team_account_id}:root" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "invoker" {
  name = "bedrock-invoke"
  role = aws_iam_role.invoker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "BedrockForAssistant"
      Effect = "Allow"
      Action = [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ApplyGuardrail",
      ]
      Resource = "*" # cross-region profiles route to several regions; narrow after the demo
    }]
  })
}

# Attach this to whatever in the TEAM account must call Bedrock (a developer's IAM role for local runs;
# the ECS task role gets an equivalent inline policy in modules/ecs_service).
data "aws_iam_policy_document" "team_assume_invoker" {
  statement {
    actions   = ["sts:AssumeRole"]
    resources = [aws_iam_role.invoker.arn]
  }
}
