# Deployment

**CI is operational.** Every pull request to `main` and every push to `main` runs the test suites, the offline
evaluations, the frontend checks and Terraform validation.
**CD is defined as code and triggered manually.** The hackathon's final release goes through the manual deployment
path that was already validated. This is a deliberate risk-control decision. The deployment architecture is
complete.

| | |
| --- | --- |
| Backend | `backend/Dockerfile` image, ECR (`backend_ecr_repository_url`), ECS Fargate, set by Terraform |
| Frontend | Vite build in a private S3 bucket behind CloudFront (`frontend_bucket_name`, `frontend_https_url`) |
| Infrastructure | Terraform, `infrastructure/terraform/envs/dev`, state in the S3 bucket from `bootstrap/` |

Terraform is the only thing that changes the ECS service. The task definition's image is
`<ECR repository>:<backend_image_tag>`, and `backend_image_tag` is a required variable, so every deployment names
its image explicitly. Images are tagged with the Git commit SHA, never `latest`.

## Manual release (used for the final submission)

From the repository root, with team-account credentials (`AWS_PROFILE=cuy-loyalty`):

```bash
# 1. The same checks as CI
uv run pytest tests/unit && uv run pytest tests/evaluation
(cd frontend && npm ci && npm run lint && npm test && npm run build)

# 2. Backend image, tagged with the commit
TF=infrastructure/terraform/envs/dev
TAG=$(git rev-parse HEAD)
REPO=$(terraform -chdir=$TF output -raw backend_ecr_repository_url)
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -f backend/Dockerfile -t "$REPO:$TAG" .
docker push "$REPO:$TAG"

# 3. Roll it out through Terraform: review the plan, then apply that plan
terraform -chdir=$TF plan -var="backend_image_tag=$TAG" -out=tfplan
terraform -chdir=$TF apply tfplan

# 4. Frontend, configured from Terraform outputs (none of them is secret)
out() { terraform -chdir=$TF output -raw "$1"; }
(cd frontend && VITE_AWS_REGION=$(out aws_region) VITE_COGNITO_USER_POOL_ID=$(out cognito_user_pool_id) \
  VITE_COGNITO_CLIENT_ID=$(out cognito_client_id) VITE_API_BASE_URL=$(out backend_https_url) npm run build)
aws s3 sync frontend/dist "s3://$(out frontend_bucket_name)" --exclude index.html
aws s3 cp frontend/dist/index.html "s3://$(out frontend_bucket_name)/index.html"
```

- **Rollback.** Run step 3 again with the previous tag. ECS's deployment circuit breaker also rolls back a deployment
  whose task never becomes healthy.
- **Frontend caching.** No CloudFront invalidation is needed. The distribution sets `Cache-Control` itself: hashed
  `/assets/*` files are cached for a year and everything else is `no-cache`. `index.html` is uploaded last, so it
  never refers to assets that aren't there yet.

## CI: `.github/workflows/ci.yml`

Triggers: `pull_request` to `main`, `push` to `main`. Permissions: `contents: read` only. It uses no AWS credentials
and calls no AWS service.

| Job | Commands |
| --- | --- |
| Python tests and offline evaluations | `uv sync --locked`, `uv run pytest tests/unit`, `uv run pytest tests/evaluation` (with `EVAL_LIVE=0`, the live suites skip) |
| Frontend lint, tests and build | `npm ci`, `npm run lint`, `npm test`, `npm run build` (in `frontend/`) |
| Terraform format and validate | `terraform fmt -check -recursive`; `init -backend=false` and `validate` for `bootstrap`, `envs/dev`, `envs/dev-app` |

CI never runs live Bedrock or guardrail evaluations, the Spark integration tests, DynamoDB writes, ML retraining or
scoring, `terraform plan` or `terraform apply`.

## CD: `.github/workflows/cd.yml`

The only trigger is `workflow_dispatch`, so nothing deploys on a push to `main`. Inputs: `environment` (`dev`) and
`apply` (default `false`). The run summary shows the environment, the commit and the image tag.

1. **Preflight.** Refuses any ref other than `main`, and fails if the required repository variables are missing.
2. **CI.** Runs `ci.yml` unchanged.
3. **Build.** Assumes the plan role through OIDC (only the team account is accepted) and reads
   `backend_ecr_repository_url` from Terraform. It builds `backend/Dockerfile` for `linux/amd64` and pushes
   `<repo>:<commit SHA>`. A tag that already exists is never overwritten.
4. **Plan.** Runs `terraform fmt -check`, `init`, `validate` and `plan -var backend_image_tag=<commit SHA>`. Because
   the repository is public, only the list of changed resources is printed. The full plan stays on the runner.
5. **Apply and frontend** (only with `apply=true`, `CD_APPLY_ENABLED=true` and approval on the `dev` GitHub
   environment):
   - re-plans and refuses to continue if the changes differ from the reviewed plan, then applies that plan;
   - waits for the ECS service to be stable;
   - checks that the running task definition uses the new tag (a rolled-back service is also "stable") and that
     `/health` answers;
   - builds the frontend from Terraform outputs and uploads it as in step 4 above.

Without `apply`, the workflow stops after the plan. Terraform is plan-only by default. The gated apply exists, but
it stays off until the configuration below is in place.

### Why the hackathon release stays manual

The backend steps above (image tagged with the commit, ECR, `terraform plan`/`apply` with `backend_image_tag`) are how
every deployment so far was made and checked against the live environment. The frontend steps use the outputs of the
existing `static_site` hosting. CD needs new IAM in the team account (an OIDC provider and two roles). Adding it right before the submission would mean changing access to the
production-like environment when there's the least time to review it. The workflow is in the repository, so turning
it on is a configuration change, not new architecture.

### Configuration required to turn CD on

None of this exists yet, and none of it is a secret. No AWS access keys are stored in GitHub.

1. **The GitHub OIDC provider** in the team account: `token.actions.githubusercontent.com`, with audience
   `sts.amazonaws.com`.
2. **A plan role**, used by the build and plan jobs. Trust: `repo:cvljubet/factored-hackathon-2026-cuy-loyalty:ref:refs/heads/main`.
   Permissions:
   - read the state bucket, plus put and delete on the lock object `envs/dev/terraform.tfstate.tflock`;
   - read the `envs/dev-app` state, which `envs/dev` reads for the Bedrock settings;
   - the read and describe calls a plan's refresh makes;
   - push to the `cuy-loyalty-dev-backend` ECR repository only.
3. **A deploy role**, used by the apply job. Trust: `repo:cvljubet/factored-hackathon-2026-cuy-loyalty:environment:dev`.
   Permissions: what `terraform apply` of `envs/dev` needs, `ecs:Describe*`, and `s3:PutObject` on the frontend
   bucket.
4. **Repository variables:**
   - `AWS_ACCOUNT_ID`: the team account;
   - `AWS_PLAN_ROLE_ARN`;
   - `AWS_DEPLOY_ROLE_ARN`;
   - `TF_STATE_BUCKET`: `terraform -chdir=infrastructure/terraform/bootstrap output -raw state_bucket`;
   - `CD_APPLY_ENABLED=true`, set only once the next step is done.
5. **A GitHub environment `dev`** with required reviewers, limited to the `main` branch.

Manage the OIDC provider and the roles in Terraform and review them like any other IAM change.
