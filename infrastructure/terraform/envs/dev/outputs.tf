output "lake_bucket" {
  value = module.data_lake.lake_bucket
}

output "artifacts_bucket" {
  value = module.data_lake.artifacts_bucket
}

output "glue_databases" {
  value = module.catalog.database_names
}

output "athena_workgroup" {
  value = module.catalog.athena_workgroup
}

output "glue_workflow" {
  value = module.glue_etl.workflow_name
}
