variable "project" {
  type    = string
  default = "cuy-loyalty"
}

variable "env" {
  type    = string
  default = "dev"
}

variable "region" {
  type    = string
  default = "us-east-2"
}

variable "model_account_profile" {
  description = "AWS CLI profile for the account that runs Bedrock (rights to create IAM roles and Bedrock resources). Null = the team account."
  type        = string
  default     = null
}
