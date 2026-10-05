output "lake_bucket" {
  value = module.data_lake.lake_bucket
}

output "artifacts_bucket" {
  value = module.data_lake.artifacts_bucket
}

output "glue_databases" {
  value = module.catalog.database_names
}

output "agent_read_policy_arn" {
  value = module.catalog.agent_read_policy_arn
}

output "athena_workgroup" {
  value = module.catalog.athena_workgroup
}

output "dq_summary_view" {
  value = module.dq_reporting.dq_summary_view
}

output "glue_workflow" {
  value = module.glue_etl.workflow_name
}

output "aws_region" {
  value = var.region
}

output "cognito_user_pool_id" {
  value = module.auth.user_pool_id
}

output "cognito_client_id" {
  value = module.auth.user_pool_client_id
}

output "cognito_issuer_url" {
  value = module.auth.issuer_url
}

output "backend_ecr_repository_url" {
  value = module.backend_registry.repository_url
}

output "backend_ecs_cluster" {
  value = module.backend_service.cluster_name
}

output "backend_ecs_service" {
  value = module.backend_service.service_name
}

output "backend_alb_dns_name" {
  value = module.backend_service.alb_dns_name
}

output "backend_log_group" {
  value = module.backend_service.log_group_name
}

output "backend_cloudfront_distribution_id" {
  value = module.backend_cdn.distribution_id
}

output "backend_cloudfront_domain" {
  value = module.backend_cdn.domain_name
}

# Use this from the frontend (VITE_API_BASE_URL) and for testing.
output "backend_https_url" {
  value = "https://${module.backend_cdn.domain_name}"
}

output "frontend_bucket_name" {
  value = module.frontend_site.bucket_name
}

output "frontend_cloudfront_distribution_id" {
  value = module.frontend_site.distribution_id
}

output "frontend_cloudfront_domain" {
  value = module.frontend_site.domain_name
}

# Add this origin to backend_cors_origins once known.
output "frontend_https_url" {
  value = "https://${module.frontend_site.domain_name}"
}

output "backend_agent_llm" {
  description = "local, or bedrock when bedrock_state_bucket is set."
  value       = local.agent_environment["AGENT_LLM"]
}
