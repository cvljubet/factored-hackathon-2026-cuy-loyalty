variable "project" {
  type    = string
  default = "cuy-loyalty"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "us-east-2"
}

# Required on every plan/apply, so each deployment names its image explicitly (CI/CD passes it):
#   terraform plan -var="backend_image_tag=<git sha>"
variable "backend_image_tag" {
  description = "Tag of the backend image in ECR to deploy, e.g. a Git SHA."
  type        = string

  validation {
    condition     = length(trimspace(var.backend_image_tag)) > 0
    error_message = "backend_image_tag must name an image tag in the backend ECR repository."
  }
}

# 0 stops the backend (and its Bedrock calls) without destroying anything; 1 starts it again.
# 2 is safe: sessions are in DynamoDB and a stale save is refused (version check), not lost.
variable "backend_desired_count" {
  description = "Backend tasks: 1 or 2 to run, 0 to stop without destroying resources."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1, 2], var.backend_desired_count)
    error_message = "backend_desired_count must be 0, 1 or 2."
  }
}

# Two free ranges in the default VPC (172.31.0.0/16; its default subnets use the first /20s)
# for the internal load balancer's private subnets.
variable "backend_private_subnet_cidrs" {
  description = "CIDRs of the two private subnets for the internal backend load balancer."
  type        = list(string)
  default     = ["172.31.128.0/24", "172.31.129.0/24"]

  validation {
    condition     = length(var.backend_private_subnet_cidrs) == 2 && alltrue([for cidr in var.backend_private_subnet_cidrs : can(cidrhost(cidr, 0))])
    error_message = "backend_private_subnet_cidrs must be two valid CIDRs inside the default VPC."
  }
}

variable "backend_cors_origins" {
  description = "Browser origins allowed to call the backend (CORS_ALLOW_ORIGINS)."
  type        = list(string)
  default = [
    "http://localhost:5173",                 # local Vite dev server
    "https://d1u14dr29pbaaw.cloudfront.net", # deployed frontend (frontend_https_url)
  ]

  validation {
    condition     = !contains(var.backend_cors_origins, "*")
    error_message = "List explicit origins; a wildcard (*) is not allowed."
  }
}

# Bedrock for the agent; envs/dev-app must be applied first. false = AGENT_LLM=local, the
# deterministic stand-in that needs no model access.
variable "bedrock_enabled" {
  description = "Run the agent on Bedrock (needs envs/dev-app applied). false = AGENT_LLM=local."
  type        = bool
  default     = true
}

variable "bedrock_router_model_id" {
  # Not the global. profile: on 2026-10-05 it answered ServiceUnavailable to every call from the
  # model account, and HybridRouter then fell back to the rules after each failed attempt.
  description = "Router model (Bedrock Haiku 4.5, US cross-region profile; shares its quota with the inquiry model)."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "bedrock_max_attempts" {
  # Throttling comes back at once and retries back off by a second or two, so a third attempt
  # rides out the model account's low per-minute quota well within CloudFront's 60 s.
  description = "Attempts per Bedrock call, including the first (BEDROCK_MAX_ATTEMPTS)."
  type        = number
  default     = 3
}

variable "bedrock_inquiry_fallback_model_id" {
  description = "Inquiry fallback model when Bedrock is on; must be one the model account can invoke (Sonnet 5 and 5.5 are not). Empty = no fallback."
  type        = string
  default     = "us.anthropic.claude-sonnet-4-6"
}
