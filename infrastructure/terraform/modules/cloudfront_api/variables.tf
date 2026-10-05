variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "origin_domain_name" {
  description = "DNS name of the HTTP origin, e.g. the backend load balancer."
  type        = string
}

# A separate flag because count can't depend on the ALB's ARN, which is unknown until the ALB exists.
variable "use_vpc_origin" {
  description = "Reach an internal ALB (vpc_origin_alb_arn) through a CloudFront VPC origin instead of a public HTTP origin."
  type        = bool
  default     = false
}

variable "vpc_origin_alb_arn" {
  description = "ARN of the internal ALB, required when use_vpc_origin is true."
  type        = string
  default     = null

  validation {
    condition     = !var.use_vpc_origin || var.vpc_origin_alb_arn != null
    error_message = "Set vpc_origin_alb_arn when use_vpc_origin is true."
  }
}

variable "comment" {
  type    = string
  default = ""
}

variable "price_class" {
  description = "PriceClass_All includes South American edge locations, closest to the bank's customers."
  type        = string
  default     = "PriceClass_All"
}

variable "origin_read_timeout" {
  description = "Seconds CloudFront waits for the origin to respond (max 60 without a quota increase)."
  type        = number
  default     = 60
}
