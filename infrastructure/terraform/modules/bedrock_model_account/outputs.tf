output "guardrail_id" {
  value = aws_bedrock_guardrail.assistant.guardrail_id
}

output "guardrail_version" {
  value = aws_bedrock_guardrail_version.assistant.version
}

output "invoker_role_arn" {
  description = "Set as BEDROCK_ROLE_ARN in the ECS task (docker-entrypoint.sh turns it into a profile)."
  value       = aws_iam_role.invoker.arn
}

output "team_assume_policy_json" {
  description = "Policy document for the team-account principal that calls Bedrock."
  value       = data.aws_iam_policy_document.team_assume_invoker.json
}
