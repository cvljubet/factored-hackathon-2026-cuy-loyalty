output "user_pool_id" {
  value = aws_cognito_user_pool.this.id
}

output "user_pool_arn" {
  value = aws_cognito_user_pool.this.arn
}

output "user_pool_client_id" {
  value = aws_cognito_user_pool_client.spa.id
}

# Token issuer for JWT validation in the backend; signing keys are at
# <issuer_url>/.well-known/jwks.json.
output "issuer_url" {
  value = "https://${aws_cognito_user_pool.this.endpoint}"
}
