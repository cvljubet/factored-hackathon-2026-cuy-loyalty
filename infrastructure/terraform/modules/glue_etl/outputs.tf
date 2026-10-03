output "job_names" {
  value = { for k, j in aws_glue_job.this : k => j.name }
}

output "workflow_name" {
  value = aws_glue_workflow.medallion.name
}
