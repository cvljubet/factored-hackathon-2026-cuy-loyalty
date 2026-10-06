# GitHub Workflows

| Workflow | Trigger | AWS access |
| --- | --- | --- |
| `ci.yml` | pull request to `main`, push to `main`, called by `cd.yml` | none |
| `cd.yml` | `workflow_dispatch` only | OIDC roles; stops after `terraform plan` unless apply is enabled and approved |

[docs/deployment.md](../../docs/deployment.md) describes both workflows.

No secrets are needed. `cd.yml` reads these repository **variables**, which aren't secret:

- `AWS_ACCOUNT_ID`
- `AWS_PLAN_ROLE_ARN`
- `AWS_DEPLOY_ROLE_ARN`
- `TF_STATE_BUCKET`
- `CD_APPLY_ENABLED`

It also uses the GitHub environment `dev`, which needs required reviewers. Never store AWS access keys in GitHub.
