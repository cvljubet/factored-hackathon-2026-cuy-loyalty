variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete non-empty buckets. Keep true only for throwaway envs."
  type        = bool
  default     = false
}
