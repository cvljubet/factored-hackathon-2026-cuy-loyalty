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

# The load balancer is reached through CloudFront. This only adds optional direct HTTP access
# (e.g. a team IP for debugging); never send Cognito tokens over it.
variable "backend_allowed_cidrs" {
  description = "Extra CIDRs allowed to reach the backend load balancer directly over HTTP. Empty = CloudFront only."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.backend_allowed_cidrs : can(cidrhost(cidr, 0))])
    error_message = "backend_allowed_cidrs must be a list of valid CIDRs, e.g. [\"203.0.113.10/32\"]."
  }

  validation {
    condition     = !contains(var.backend_allowed_cidrs, "0.0.0.0/0")
    error_message = "The HTTP load balancer must not be open to the whole internet (0.0.0.0/0)."
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
