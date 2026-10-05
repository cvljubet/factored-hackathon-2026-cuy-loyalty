variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "service_name" {
  description = "Service name appended to the prefix, e.g. backend -> cuy-loyalty-dev-backend."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Public subnets (at least two AZs) for the load balancer and the task."
  type        = list(string)
}

variable "image" {
  description = "Full image reference, <repository_url>:<tag>."
  type        = string
}

variable "container_port" {
  type    = number
  default = 8000
}

variable "cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "memory" {
  description = "Fargate memory in MiB; must be a valid pairing with cpu."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Running tasks. 1 while sessions live in process memory."
  type        = number
  default     = 1
}

variable "health_check_path" {
  type    = string
  default = "/health"
}

variable "environment" {
  description = "Plain (non-secret) environment variables for the container."
  type        = map(string)
  default     = {}
}

variable "allowed_cidrs" {
  description = "CIDRs allowed to reach the load balancer on HTTP port 80 (e.g. temporary direct access)."
  type        = list(string)
  default     = []
}

variable "allowed_prefix_list_ids" {
  description = "Managed prefix lists allowed to reach the load balancer on HTTP port 80 (e.g. CloudFront origin-facing)."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  type    = number
  default = 7
}
