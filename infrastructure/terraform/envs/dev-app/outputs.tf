# Read by envs/dev through terraform_remote_state; keep these names stable.

output "bedrock_region" {
  value = var.region
}

output "bedrock_invoker_role_arn" {
  description = "Role in the model account that the backend assumes (BEDROCK_ROLE_ARN)."
  value       = module.bedrock.invoker_role_arn
}

output "bedrock_guardrail_id" {
  value = module.bedrock.guardrail_id
}

output "bedrock_guardrail_version" {
  value = module.bedrock.guardrail_version
}

output "team_assume_policy_json" {
  description = "Policy for a team-account principal (e.g. a developer role) that must assume the invoker role."
  value       = module.bedrock.team_assume_policy_json
}
