variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "origin_domain_name" {
  description = "DNS name of the HTTP origin, e.g. the backend load balancer."
  type        = string
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
