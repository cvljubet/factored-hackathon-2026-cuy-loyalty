variable "name_prefix" {
  type = string
}

variable "lake_bucket" {
  type = string
}

variable "artifacts_bucket" {
  type = string
}

variable "glue_role_arn" {
  type = string
}

variable "database_names" {
  description = "Map of layer -> Glue database name (bronze, silver, gold)."
  type        = map(string)
}

variable "scripts_dir" {
  description = "Local folder with the PySpark scripts."
  type        = string
}

variable "worker_type" {
  type    = string
  default = "G.1X"
}

variable "number_of_workers" {
  type    = number
  default = 4
}
