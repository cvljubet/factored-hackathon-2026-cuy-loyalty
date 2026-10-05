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

# Later tasks plug in here the same way, e.g.:
# module "serving"  { source = "../../modules/dynamodb" ... }  # recommendations for the agent
# module "app"      { source = "../../modules/ecs_service" ... } # backend + agent API
