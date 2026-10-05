output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_name" {
  value = aws_ecs_service.this.name
}

# For a CloudFront VPC origin.
output "alb_arn" {
  value = aws_lb.this.arn
}

output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.this.name
}

# Attach application permissions (Bedrock, DynamoDB) to this role later.
output "task_role_name" {
  value = aws_iam_role.task.name
}

output "alb_security_group_id" {
  value = aws_security_group.alb.id
}
