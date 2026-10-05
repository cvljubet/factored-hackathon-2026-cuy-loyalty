output "database_names" {
  value = { for k, db in aws_glue_catalog_database.layer : k => db.name }
}

output "bronze_crawler_name" {
  value = aws_glue_crawler.bronze.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.this.name
}

output "agent_read_policy_arn" {
  description = "Attach to the role that serves the assistant (the backend's ECS task role)."
  value       = aws_iam_policy.agent_read.arn
}
