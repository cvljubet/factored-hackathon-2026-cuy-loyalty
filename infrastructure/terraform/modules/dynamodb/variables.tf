variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "table_name" {
  description = "Table name appended to the prefix, e.g. customer-serving -> cuy-loyalty-dev-customer-serving."
  type        = string
}

variable "ttl_attribute" {
  description = "Attribute holding an expiry time in epoch seconds; empty = no TTL."
  type        = string
  default     = ""
}

variable "deletion_protection" {
  description = "Block deleting the table. Keep false only for throwaway envs."
  type        = bool
  default     = true
}
