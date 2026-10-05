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

# CloudFront's origin-facing addresses: the only source the backend load balancer needs.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# Backend + agent API on ECS Fargate behind an HTTP ALB that only CloudFront (plus any optional
# backend_allowed_cidrs) can reach. One task: sessions and handoffs are in process memory.
module "backend_service" {
  source                  = "../../modules/ecs_service"
  name_prefix             = local.name_prefix
  service_name            = "backend"
  vpc_id                  = data.aws_vpc.default.id
  subnet_ids              = slice(sort(data.aws_subnets.default.ids), 0, 2)
  image                   = "${module.backend_registry.repository_url}:${var.backend_image_tag}"
  cpu                     = 256  # 0.25 vCPU
  memory                  = 1024 # 1 GB
  desired_count           = 1
  allowed_prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id]
  allowed_cidrs           = var.backend_allowed_cidrs

  environment = merge({
    COGNITO_REGION        = var.region
    COGNITO_USER_POOL_ID  = module.auth.user_pool_id
    COGNITO_APP_CLIENT_ID = module.auth.user_pool_client_id
    CORS_ALLOW_ORIGINS    = jsonencode(var.backend_cors_origins)
  }, local.agent_environment)
}

# Bedrock (opt-in with -var bedrock_state_bucket=<state bucket>). envs/dev-app creates the
# guardrail and the invoker role in the model account; apply it first. The task role may only
# assume that role, and backend/docker-entrypoint.sh turns BEDROCK_ROLE_ARN into the AWS profile
# the backend's Bedrock client uses (BEDROCK_PROFILE); other AWS calls keep the task role.
data "terraform_remote_state" "app" {
  count   = var.bedrock_state_bucket == null ? 0 : 1
  backend = "s3"
  config = {
    bucket = var.bedrock_state_bucket
    key    = "envs/dev-app/terraform.tfstate"
    region = "us-east-2"
  }
}

locals {
  bedrock = var.bedrock_state_bucket == null ? null : data.terraform_remote_state.app[0].outputs

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

# HTTPS for the backend on the default *.cloudfront.net domain; nothing is cached.
module "backend_cdn" {
  source             = "../../modules/cloudfront_api"
  name_prefix        = local.name_prefix
  origin_domain_name = module.backend_service.alb_dns_name
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

# Later tasks plug in here the same way, e.g.:
# module "serving"  { source = "../../modules/dynamodb" ... }  # recommendations for the agent
