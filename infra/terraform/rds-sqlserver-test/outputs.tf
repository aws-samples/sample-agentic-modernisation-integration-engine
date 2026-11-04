output "endpoint" {
  description = "SQL Server host name."
  value       = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "master_username" {
  value = aws_db_instance.this.username
}

output "master_secret_arn" {
  description = "RDS-managed Secrets Manager secret holding the master password."
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.bucket
}

output "engine_version" {
  value = aws_db_instance.this.engine_version_actual
}

output "next_step" {
  value = "Run ./restore-wwi.sh to load WideWorldImporters, then follow its printed .env instructions."
}

output "region" {
  value = var.region
}
