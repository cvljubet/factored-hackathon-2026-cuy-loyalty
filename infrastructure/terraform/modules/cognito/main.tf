# User pool for the React SPA: users sign in with email + password via SRP from
# the app's own login form, so there is no hosted UI, domain or identity pool.
# Users are created by an admin (no self-sign-up), each carrying the
# custom:customer_id that links them to their record in the customer dataset.
resource "aws_cognito_user_pool" "this" {
  name                = "${var.name_prefix}-users"
  deletion_protection = var.deletion_protection ? "ACTIVE" : "INACTIVE"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  username_configuration {
    case_sensitive = false
  }

  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  password_policy {
    minimum_length                   = 8
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  # Schema changes to an existing attribute force a new pool (and lose every
  # user), so customer_id is defined from the start. Mutable so an admin can
  # correct it; the SPA client below cannot write it.
  schema {
    name                     = "customer_id"
    attribute_data_type      = "String"
    mutable                  = true
    required                 = false
    developer_only_attribute = false

    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }

  mfa_configuration = "OFF"
}

# Public client for the SPA: a browser cannot keep a secret.
resource "aws_cognito_user_pool_client" "spa" {
  name         = "${var.name_prefix}-spa"
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret               = false
  explicit_auth_flows           = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = var.access_token_validity_minutes
  id_token_validity      = var.id_token_validity_minutes
  refresh_token_validity = var.refresh_token_validity_days

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  # customer_id is readable (it is in the ID token) but not writable from the
  # SPA; otherwise a user could point their account at another customer's data.
  read_attributes  = ["email", "email_verified", "given_name", "family_name", "custom:customer_id"]
  write_attributes = ["given_name", "family_name"]
}
