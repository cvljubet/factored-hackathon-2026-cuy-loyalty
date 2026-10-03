# Terraform

Infrastructure for the medallion data lake (S3 + Glue + Athena + IAM). Later
stacks (DynamoDB, ECS, ECR) are added as new modules under `modules/` and
wired into `envs/dev/main.tf`.

```
bootstrap/          one-time: S3 bucket for Terraform state
envs/dev/           the environment we deploy; composes the modules
modules/data_lake/  lake bucket (bronze/ silver/ gold/) + artifacts bucket
modules/iam/        Glue role, least privilege per layer
modules/catalog/    Glue databases per layer, bronze crawler, Athena workgroup
modules/glue_etl/   PySpark jobs (data/pipelines/glue) + workflow silver -> gold
```

## Deploy

Requires Terraform >= 1.10 and AWS credentials for the team account.

```bash
# 1. Once: the state bucket
cd infrastructure/terraform/bootstrap
terraform init && terraform apply
STATE_BUCKET=$(terraform output -raw state_bucket)

# 2. The data platform
cd ../envs/dev
terraform init -backend-config="bucket=$STATE_BUCKET"
terraform plan
terraform apply

# 3. Load raw data into bronze, then run the pipeline
cd ../../../..
SOURCE_URI=s3://<organizers-bucket>/data LAKE_BUCKET=$(terraform -chdir=infrastructure/terraform/envs/dev output -raw lake_bucket) \
  ./scripts/ingest_to_bronze.sh
aws glue start-workflow-run --name $(terraform -chdir=infrastructure/terraform/envs/dev output -raw glue_workflow)
```

Editing a script in `data/pipelines/glue/` and running `terraform apply` re-uploads it.

## Rules

- Never commit state, `.tfvars` with secrets, or the organizers' access keys.
  The state bucket name is passed at `init` time.
- Commit `.terraform.lock.hcl` so everyone uses the same provider version.
- `terraform destroy` in `envs/dev` removes everything, data included
  (`force_destroy = true` on the dev buckets).
