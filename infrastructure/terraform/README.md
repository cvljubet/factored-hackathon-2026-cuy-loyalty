# Terraform

Infrastructure for the medallion data lake (S3 + Glue + Athena + IAM) and user
authentication (Cognito). Later stacks (DynamoDB, ECS, ECR) are added as new
modules under `modules/` and wired into `envs/dev/main.tf`.

```
bootstrap/          one-time: S3 bucket for Terraform state
envs/dev/           the environment we deploy; composes the modules
envs/dev-app/       Bedrock in the model account (own state; read by envs/dev)
modules/data_lake/  lake bucket (bronze/ silver/ gold/) + artifacts bucket
modules/iam/        Glue role, least privilege per layer
modules/catalog/    Glue databases per layer, bronze crawler, Athena workgroup
modules/glue_etl/   PySpark jobs (data/pipelines/glue) + workflow silver -> gold
modules/cognito/    user pool + SPA app client for the React login
modules/ecr/        container registry per service image (backend API)
modules/ecs_service/ Fargate service + ALB + security groups + IAM roles + logs (backend API)
modules/cloudfront_api/ HTTPS CloudFront distribution in front of the backend ALB (no caching)
modules/static_site/ private S3 bucket + CloudFront (OAC, SPA routing) for the React frontend
modules/bedrock_model_account/ guardrail, invoker role and invocation logging in the Bedrock (model) account
```

`envs/dev` has one required variable (no default), so every plan/apply names the
backend image explicitly:

```bash
terraform plan -var="backend_image_tag=<git sha in ECR>"
```

Clients use `terraform output -raw backend_https_url` (CloudFront, HTTPS). The load
balancer behind it speaks HTTP and accepts only CloudFront's origin-facing prefix list.
`backend_allowed_cidrs` optionally adds direct HTTP access for debugging (empty by
default); never send Cognito tokens over it.

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

## Authentication (Cognito)

`modules/cognito` creates a user pool and a public app client for the React SPA.
Users sign in with email + password (SRP) from the app's own login form, so there
is no hosted UI, domain, identity pool or callback URL.

- **No self-sign-up.** Users are created by an admin, never in Terraform, so no
  passwords end up in variables or state.
- **`custom:customer_id`** maps each user to their `customer_id` in the customer
  dataset and is included in the ID token. The SPA client can read it but not
  write it, so users cannot repoint their account; only admins can change it.
- **Do not change the pool's attribute schema after creation.** Modifying an
  existing attribute forces Terraform to replace the pool, deleting every user.

Values for the apps (none of them are secret):

| Output                 | Used by                                                    |
|------------------------|------------------------------------------------------------|
| `aws_region`           | frontend (`VITE_AWS_REGION`)                               |
| `cognito_user_pool_id` | frontend (`VITE_COGNITO_USER_POOL_ID`)                     |
| `cognito_client_id`    | frontend (`VITE_COGNITO_CLIENT_ID`), backend               |
| `cognito_issuer_url`   | backend JWT validation (`<issuer>/.well-known/jwks.json`) |

Create a user. Type the password at the prompt; don't commit it or leave it in
shell history:

```bash
POOL_ID=$(terraform -chdir=infrastructure/terraform/envs/dev output -raw cognito_user_pool_id)

aws cognito-idp admin-create-user --user-pool-id "$POOL_ID" \
  --username user@example.com --message-action SUPPRESS \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true \
                    Name=given_name,Value=<first-name> Name=custom:customer_id,Value=<customer_id>

read -rs -p "Password: " PASSWORD && echo
aws cognito-idp admin-set-user-password --user-pool-id "$POOL_ID" \
  --username user@example.com --password "$PASSWORD" --permanent
```

## Bedrock (`envs/dev-app`)

The team account's Bedrock quotas are close to zero, so the models run in a second AWS account.
`envs/dev-app` is a separate root with its own state (`envs/dev-app/terraform.tfstate` in the same
bucket), so it never locks or changes `envs/dev`, and only whoever applies it needs model-account
credentials. Its provider `aws.model` uses `-var model_account_profile=<profile>`; unset, it uses the
team account.

`module.bedrock` creates, in the model account:

- the guardrail (Standard tier, Spanish and Portuguese): content filters, prompt-attack filter on
  input, five denied topics, and **Block** for card numbers, CVV, PIN and IBAN, plus a published
  version (editing the guardrail publishes a new one);
- the `cuy-bedrock-invoker` role, trusted by the team account, with `bedrock:InvokeModel`,
  `InvokeModelWithResponseStream` and `ApplyGuardrail`;
- model invocation logging to CloudWatch (`/bedrock/<prefix>`, 14 days; one configuration per account
  and Region, so set `enable_invocation_logging = false` to keep an existing one).

### Credentials

Two profiles in `~/.aws/config` (never in the repo). An admin one for Terraform, for example a role in
the model account assumed from the team account (or `aws configure sso` if the account is in IAM
Identity Center):

```ini
[profile cuy-model-admin]
role_arn       = arn:aws:iam::<MODEL_ACCOUNT_ID>:role/<AdminRoleName>
source_profile = cuy-loyalty
region         = us-east-2
```

And, after the first apply, one that assumes the invoker role, for local runs and
`scripts/bedrock_smoke.py` (`BEDROCK_PROFILE=cuy-bedrock`); ECS does the same with the task role:

```ini
[profile cuy-bedrock]
role_arn       = arn:aws:iam::<MODEL_ACCOUNT_ID>:role/cuy-bedrock-invoker
source_profile = cuy-loyalty
region         = us-east-2
```

### Order

1. Once per model account: in the Bedrock console (Ohio) an admin sends one message to Claude Haiku 4.5
   (Anthropic asks for use-case details on first use), checks that the model answers from the CLI
   (`aws bedrock-runtime converse --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0 --messages '[{"role":"user","content":[{"text":"ok"}]}]' --region us-east-2 --profile <admin profile>`), and checks the quotas:
   `aws service-quotas list-service-quotas --service-code bedrock --region us-east-2 --profile cuy-model-admin`.
2. Apply `envs/dev-app` with team-account credentials (state bucket) plus the admin profile:

   ```bash
   cd infrastructure/terraform/envs/dev-app
   terraform init -backend-config="bucket=$STATE_BUCKET"
   terraform apply -var model_account_profile=cuy-model-admin
   ```

3. Smoke test from your machine:
   `BEDROCK_PROFILE=cuy-bedrock BEDROCK_GUARDRAIL_ID=$(terraform output -raw bedrock_guardrail_id) BEDROCK_GUARDRAIL_VERSION=$(terraform output -raw bedrock_guardrail_version) uv run python scripts/bedrock_smoke.py` (from the repo root).
4. Switch the backend over: apply `envs/dev` with `-var bedrock_state_bucket=$STATE_BUCKET` and an
   image built from this branch (its entrypoint writes the `bedrock` profile). That gives the task role
   `sts:AssumeRole` on the invoker role and sets `AGENT_LLM`/`AGENT_ROUTER=bedrock`, `BEDROCK_ROLE_ARN`,
   `BEDROCK_PROFILE` and the guardrail ID and version. Without the variable the backend stays on
   `AGENT_LLM=local`, exactly as before. The inquiry fallback is Sonnet 4.6 (Sonnet 5 and 5.5 are
   not available to the model account); change it with `-var bedrock_inquiry_fallback_model_id=<id>`
   after the same `converse` check, or turn it off with an empty value.

## Rules

- Never commit state, `.tfvars` with secrets, or the organizers' access keys.
  The state bucket name is passed at `init` time.
- Commit `.terraform.lock.hcl` so everyone uses the same provider version.
- `terraform destroy` in `envs/dev` removes everything, data and Cognito users
  included (`force_destroy = true` on the dev buckets, deletion protection off
  on the dev user pool).
