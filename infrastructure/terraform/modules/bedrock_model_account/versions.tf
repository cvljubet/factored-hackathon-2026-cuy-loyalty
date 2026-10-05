terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0" # tier_config and cross_region_config need a recent 6.x
    }
  }
}
