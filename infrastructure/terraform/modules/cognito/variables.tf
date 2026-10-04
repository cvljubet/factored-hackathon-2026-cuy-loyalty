variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "deletion_protection" {
  description = "Block deleting the user pool. Keep false only for throwaway envs."
  type        = bool
  default     = true
}

variable "access_token_validity_minutes" {
  type    = number
  default = 60
}

variable "id_token_validity_minutes" {
  type    = number
  default = 60
}

variable "refresh_token_validity_days" {
  type    = number
  default = 30
}
