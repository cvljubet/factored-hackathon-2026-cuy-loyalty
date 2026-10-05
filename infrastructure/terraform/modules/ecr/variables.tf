variable "name_prefix" {
  description = "Prefix for resource names, e.g. cuy-loyalty-dev."
  type        = string
}

variable "repository_name" {
  description = "Service name appended to the prefix, e.g. backend -> cuy-loyalty-dev-backend."
  type        = string
}

variable "image_tag_mutability" {
  description = "MUTABLE lets a tag such as latest be re-pushed; IMMUTABLE forbids overwriting tags."
  type        = string
  default     = "MUTABLE"
}

variable "keep_images" {
  description = "How many of the most recent images to keep; older ones are expired."
  type        = number
  default     = 10
}

variable "force_delete" {
  description = "Allow terraform destroy to delete the repository with images in it. Keep true only for throwaway envs."
  type        = bool
  default     = false
}
