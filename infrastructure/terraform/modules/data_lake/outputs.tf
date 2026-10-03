output "lake_bucket" {
  value = aws_s3_bucket.this["lake"].bucket
}

output "lake_bucket_arn" {
  value = aws_s3_bucket.this["lake"].arn
}

output "artifacts_bucket" {
  value = aws_s3_bucket.this["artifacts"].bucket
}

output "artifacts_bucket_arn" {
  value = aws_s3_bucket.this["artifacts"].arn
}
