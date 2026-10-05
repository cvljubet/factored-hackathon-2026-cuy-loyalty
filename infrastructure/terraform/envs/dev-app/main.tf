# Bedrock for the assistant, in the model account: guardrail, invoker role and invocation logging.
# Kept apart from envs/dev so only this stack needs model-account credentials. envs/dev reads its
# outputs (remote state) to point the backend at Bedrock; apply this stack first.

locals {
  name_prefix = "${var.project}-${var.env}"
  tags = {
    Project     = var.project
    Environment = var.env
    ManagedBy   = "terraform"
    Stack       = "dev-app"
  }
}

data "aws_caller_identity" "team" {}

module "bedrock" {
  source          = "../../modules/bedrock_model_account"
  providers       = { aws = aws.model }
  name_prefix     = local.name_prefix
  team_account_id = data.aws_caller_identity.team.account_id
  # Prefixed: guardrail names are unique per account, and "cuy-assistant" is taken by the
  # guardrail made by hand in the console.
  guardrail_name = "${local.name_prefix}-assistant"
}
