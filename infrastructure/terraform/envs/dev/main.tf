locals {
  name_prefix = "${var.project}-${var.env}"
}

module "data_lake" {
  source        = "../../modules/data_lake"
  name_prefix   = local.name_prefix
  force_destroy = true # hackathon env: allow teardown
}

module "iam" {
  source               = "../../modules/iam"
  name_prefix          = local.name_prefix
  lake_bucket_arn      = module.data_lake.lake_bucket_arn
  artifacts_bucket_arn = module.data_lake.artifacts_bucket_arn
}

module "catalog" {
  source           = "../../modules/catalog"
  name_prefix      = local.name_prefix
  lake_bucket      = module.data_lake.lake_bucket
  artifacts_bucket = module.data_lake.artifacts_bucket
  crawler_role_arn = module.iam.glue_role_arn
}

# Glue tables over the data quality reports, and the dq_summary view in Athena.
module "dq_reporting" {
  source         = "../../modules/dq_reporting"
  lake_bucket    = module.data_lake.lake_bucket
  database_names = module.catalog.database_names
}

module "glue_etl" {
  source           = "../../modules/glue_etl"
  name_prefix      = local.name_prefix
  lake_bucket      = module.data_lake.lake_bucket
  artifacts_bucket = module.data_lake.artifacts_bucket
  glue_role_arn    = module.iam.glue_role_arn
  database_names   = module.catalog.database_names
  scripts_dir      = "${path.root}/../../../../data/pipelines/glue"
}

module "auth" {
  source              = "../../modules/cognito"
  name_prefix         = local.name_prefix
  deletion_protection = false # hackathon env: allow teardown
}

# Registry for the backend API image (backend/Dockerfile).
module "backend_registry" {
  source          = "../../modules/ecr"
  name_prefix     = local.name_prefix
  repository_name = "backend"
  force_delete    = true # hackathon env: allow teardown
}

# The account's default VPC and its public subnets (looked up, not managed here).
# Two AZs are enough for the load balancer and keep public IPv4 charges down.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

locals {
  # The task runs in two of the default (public) subnets: a public IP gives it outbound access
  # to ECR, Cognito, STS and Bedrock without a NAT gateway; nothing can connect in.
  backend_task_subnet_ids = slice(sort(data.aws_subnets.default.ids), 0, 2)
}

data "aws_subnet" "backend_task" {
  count = 2
  id    = local.backend_task_subnet_ids[count.index]
}

# Private subnets for the internal load balancer, in the same AZs as the task (an ALB only
# routes to targets in its own AZs). Their route table has only the VPC's local route, so
# nothing in them is reachable from the internet. CloudFront gets in through its VPC origin.
resource "aws_subnet" "backend_private" {
  count             = 2
  vpc_id            = data.aws_vpc.default.id
  availability_zone = data.aws_subnet.backend_task[count.index].availability_zone
  cidr_block        = var.backend_private_subnet_cidrs[count.index]
  tags              = { Name = "${local.name_prefix}-backend-private-${data.aws_subnet.backend_task[count.index].availability_zone}" }
}

resource "aws_route_table" "backend_private" {
  vpc_id = data.aws_vpc.default.id
  tags   = { Name = "${local.name_prefix}-backend-private" }
}

resource "aws_route_table_association" "backend_private" {
  count          = 2
  subnet_id      = aws_subnet.backend_private[count.index].id
  route_table_id = aws_route_table.backend_private.id
}

# Backend + agent API on ECS Fargate behind an internal HTTP ALB that only CloudFront reaches,
# through its VPC origin, so the Cognito token never crosses the internet in clear.
# One task: sessions and handoffs are in process memory.
module "backend_service" {
  source         = "../../modules/ecs_service"
  name_prefix    = local.name_prefix
  service_name   = "backend"
  vpc_id         = data.aws_vpc.default.id
  subnet_ids     = local.backend_task_subnet_ids
  internal       = true
  alb_subnet_ids = aws_subnet.backend_private[*].id
  image          = "${module.backend_registry.repository_url}:${var.backend_image_tag}"
  cpu            = 256  # 0.25 vCPU
  memory         = 1024 # 1 GB
  desired_count  = var.backend_desired_count
  # CloudFront's VPC origin connects from a network interface inside the VPC; the ALB is
  # internal, so nothing outside the VPC can reach it either way.
  allowed_cidrs = [data.aws_vpc.default.cidr_block]

  environment = merge({
    COGNITO_REGION        = var.region
    COGNITO_USER_POOL_ID  = module.auth.user_pool_id
    COGNITO_APP_CLIENT_ID = module.auth.user_pool_client_id
    CORS_ALLOW_ORIGINS    = jsonencode(var.backend_cors_origins)
  }, local.serving_environment, local.agent_environment)
}

# The agent's tools and GET /me/profile read the customer-serving table with the task role (no
# SERVING_AWS_PROFILE: the default credential chain), never through the Bedrock profile.
locals {
  serving_environment = {
    SERVING_BACKEND    = "dynamodb"
    SERVING_TABLE_NAME = module.customer_serving.table_name
    SERVING_AWS_REGION = var.region
  }
}

# Exactly what agents/serving.py calls: GetItem (PROFILE, FX pairs) and Query (on PK, by SK prefix).
resource "aws_iam_role_policy" "backend_serving_read" {
  name = "read-customer-serving"
  role = module.backend_service.task_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["dynamodb:GetItem", "dynamodb:Query"]
      Resource = module.customer_serving.table_arn
    }]
  })
}

# Bedrock (bedrock_enabled, on by default). envs/dev-app creates the guardrail and the invoker
# role in the model account and must be applied first; its state sits in the same bucket as
# this stack's (bootstrap names it <project>-tfstate-<account id>). The task role may only
# assume that role, and backend/docker-entrypoint.sh turns BEDROCK_ROLE_ARN into the AWS profile
# the backend's Bedrock client uses (BEDROCK_PROFILE); other AWS calls keep the task role.
data "aws_caller_identity" "current" {}

data "terraform_remote_state" "app" {
  count   = var.bedrock_enabled ? 1 : 0
  backend = "s3"
  config = {
    bucket = "${var.project}-tfstate-${data.aws_caller_identity.current.account_id}"
    key    = "envs/dev-app/terraform.tfstate"
    region = "us-east-2"
  }
}

locals {
  bedrock = var.bedrock_enabled ? data.terraform_remote_state.app[0].outputs : null

  agent_environment = local.bedrock == null ? tomap({ AGENT_LLM = "local" }) : tomap(merge(
    {
      AGENT_LLM                 = "bedrock"
      AGENT_ROUTER              = "bedrock"
      BEDROCK_REGION            = local.bedrock.bedrock_region
      BEDROCK_ROLE_ARN          = local.bedrock.bedrock_invoker_role_arn
      BEDROCK_PROFILE           = "bedrock"
      BEDROCK_GUARDRAIL_ID      = local.bedrock.bedrock_guardrail_id
      BEDROCK_GUARDRAIL_VERSION = local.bedrock.bedrock_guardrail_version
    },
    var.bedrock_inquiry_fallback_model_id == "" ? {} : {
      BEDROCK_INQUIRY_FALLBACK_MODEL_ID = var.bedrock_inquiry_fallback_model_id
    },
  ))
}

resource "aws_iam_role_policy" "backend_assume_bedrock" {
  count = local.bedrock == null ? 0 : 1
  name  = "assume-bedrock-invoker"
  role  = module.backend_service.task_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sts:AssumeRole"
      Resource = local.bedrock.bedrock_invoker_role_arn
    }]
  })
}

# CloudFront's VPC origin connects from network interfaces in AWS's service-managed security group,
# which AWS creates in the VPC with the first VPC origin (looked up, never managed here). The ALB
# must allow that group as a source; a CIDR rule alone is not enough.
data "aws_security_group" "cloudfront_vpc_origins" {
  vpc_id = data.aws_vpc.default.id
  name   = "CloudFront-VPCOrigins-Service-SG"
}

resource "aws_vpc_security_group_ingress_rule" "backend_alb_from_cloudfront" {
  security_group_id            = module.backend_service.alb_security_group_id
  description                  = "HTTP from CloudFront VPC origins"
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  referenced_security_group_id = data.aws_security_group.cloudfront_vpc_origins.id
}

# HTTPS for the backend on the default *.cloudfront.net domain; nothing is cached. The ALB is
# internal, so CloudFront reaches it through a VPC origin over AWS's network.
module "backend_cdn" {
  source             = "../../modules/cloudfront_api"
  name_prefix        = local.name_prefix
  origin_domain_name = module.backend_service.alb_dns_name
  use_vpc_origin     = true
  vpc_origin_alb_arn = module.backend_service.alb_arn
  comment            = "${local.name_prefix} backend API (HTTPS)"
}

# The React app (frontend/ Vite build) from a private bucket over HTTPS.
module "frontend_site" {
  source        = "../../modules/static_site"
  name_prefix   = local.name_prefix
  site_name     = "frontend"
  force_destroy = true # hackathon env: allow teardown
  comment       = "${local.name_prefix} frontend (HTTPS)"
}

# What the agent's tools read online, copied from the agent zone (gold/agent/) plus the
# recommender's current output. Customer items: PK = CUST#<customer_id>, SK = PROFILE | RECO |
# TXN#<ts>#<transaction_id> | CONTACT#<ts>#<interaction_id> | COMPLAINT#<ts>#<complaint_id> |
# CAMPAIGN#<ts>#<send_id>. Public reference data: PK = REF#BRANCH, SK = <city>#<branch_id>;
# PK = REF#FX, SK = <source_currency>#<target_currency>. Rebuilt from gold, so no TTL.
module "customer_serving" {
  source              = "../../modules/dynamodb"
  name_prefix         = local.name_prefix
  table_name          = "customer-serving"
  deletion_protection = false # hackathon env: allow teardown
}

# Persisted chat sessions. PK = CUST#<customer_id>#SESSION#<session_id>; SK = SESSION (metadata,
# failure count, language) | TURN#<ts>#<message_id> (user and assistant turns) | HANDOFF#<ts>.
# The latest turns are a Query on PK with begins_with(SK, "TURN#"), newest first. Items expire
# through expires_at (epoch seconds), e.g. 90 days after the last turn.
module "conversations" {
  source              = "../../modules/dynamodb"
  name_prefix         = local.name_prefix
  table_name          = "conversations"
  ttl_attribute       = "expires_at"
  deletion_protection = false # hackathon env: allow teardown
}
