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
