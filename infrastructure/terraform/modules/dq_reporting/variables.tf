variable "lake_bucket" {
  type = string
}

variable "database_names" {
  description = "Map of layer -> Glue database name (bronze, silver, gold)."
  type        = map(string)
}
