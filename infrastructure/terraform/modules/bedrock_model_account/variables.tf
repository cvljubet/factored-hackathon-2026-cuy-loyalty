variable "name_prefix" {
  type        = string
  description = "Used for the invocation-log group name."
}

variable "team_account_id" {
  type        = string
  description = "Account allowed to assume the invoker role (the team account)."
}

variable "guardrail_name" {
  type    = string
  default = "cuy-assistant"
}

variable "invoker_role_name" {
  type    = string
  default = "cuy-bedrock-invoker"
}

variable "enable_invocation_logging" {
  type        = bool
  default     = true
  description = "Account-wide Bedrock model invocation logging to CloudWatch (one config per account and Region; it replaces an existing one)."
}
