output "app_url" {
  description = "Open this in a browser."
  value       = "https://${aws_cloudfront_distribution.this.domain_name}"
}

output "app_username" {
  value = var.app_username
}

output "app_password" {
  description = "Read with: terraform output -raw app_password"
  value       = local.app_password
  sensitive   = true
}

output "ecr_repositories" {
  description = "Image name -> repository URL (used by build-push.sh)."
  value       = { for k, r in aws_ecr_repository.this : k => r.repository_url }
}

output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service" {
  value = aws_ecs_service.app.name
}

output "aurora_endpoint" {
  value = aws_rds_cluster.aurora.endpoint
}

output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}

output "region" {
  value = var.region
}
