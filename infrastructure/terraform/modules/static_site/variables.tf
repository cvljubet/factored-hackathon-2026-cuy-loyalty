variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "site_name" {
  description = "Site name appended to the prefix, e.g. frontend."
  type        = string
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete the bucket with files in it. Keep true only for throwaway envs."
  type        = bool
  default     = false
}

variable "hashed_assets_path" {
  description = "Path pattern of the content-hashed build files (Vite: /assets/*)."
  type        = string
  default     = "/assets/*"
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
