terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Same state bucket as envs/dev, separate key (and lock), so this stack and envs/dev never
  # block each other:
  #   terraform init -backend-config="bucket=<state_bucket>"
  backend "s3" {
    key          = "envs/dev-app/terraform.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true
  }
}

# The team account: holds the state and is the account allowed to assume the invoker role.
provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}

# The account that runs Bedrock (separate from the team account because of its quotas).
# profile = null reuses the default credentials, i.e. the model account IS the team account.
provider "aws" {
  alias   = "model"
  region  = var.region
  profile = var.model_account_profile

  default_tags {
    tags = local.tags
  }
}
