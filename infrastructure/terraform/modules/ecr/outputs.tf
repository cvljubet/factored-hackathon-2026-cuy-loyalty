output "repository_name" {
  value = aws_ecr_repository.this.name
}

# <account>.dkr.ecr.<region>.amazonaws.com/<name>; push images here and point ECS at it.
output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "repository_arn" {
  value = aws_ecr_repository.this.arn
}
