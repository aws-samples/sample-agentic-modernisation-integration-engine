# Test-only RDS for SQL Server with native backup/restore, for the AMIE validation harness.
# Not for production: no deletion protection, no final snapshot, single AZ.

data "aws_vpc" "selected" {
  default = var.vpc_id == ""
  id      = var.vpc_id == "" ? null : var.vpc_id
}

data "aws_subnets" "selected" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.selected.id]
  }
}

data "aws_rds_engine_version" "sqlserver" {
  engine = var.engine
  latest = true
}

data "aws_caller_identity" "current" {}

locals {
  subnet_ids           = length(var.subnet_ids) > 0 ? var.subnet_ids : data.aws_subnets.selected.ids
  engine_version       = data.aws_rds_engine_version.sqlserver.version_actual
  major_engine_version = join(".", slice(split(".", local.engine_version), 0, 2))
  bucket_name          = "${var.name}-backups-${data.aws_caller_identity.current.account_id}"
}

# --- S3 bucket for .bak files ------------------------------------------------------------------

resource "aws_s3_bucket" "backups" {
  bucket        = local.bucket_name
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# --- IAM role RDS uses to read backups ----------------------------------------------------------

data "aws_iam_policy_document" "rds_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

data "aws_iam_policy_document" "backup_access" {
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.backups.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListMultipartUploadParts", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.backups.arn}/*"]
  }
}

resource "aws_iam_role" "backup_restore" {
  name               = "${var.name}-backup-restore"
  assume_role_policy = data.aws_iam_policy_document.rds_assume.json
}

resource "aws_iam_role_policy" "backup_restore" {
  name   = "s3-backup-access"
  role   = aws_iam_role.backup_restore.id
  policy = data.aws_iam_policy_document.backup_access.json
}

# --- Option group: native backup/restore --------------------------------------------------------

resource "aws_db_option_group" "this" {
  name                     = "${var.name}-options"
  option_group_description = "AMIE test: SQLSERVER_BACKUP_RESTORE"
  engine_name              = var.engine
  major_engine_version     = local.major_engine_version

  option {
    option_name = "SQLSERVER_BACKUP_RESTORE"
    option_settings {
      name  = "IAM_ROLE_ARN"
      value = aws_iam_role.backup_restore.arn
    }
  }
}

# --- Network ------------------------------------------------------------------------------------

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-subnets"
  subnet_ids = local.subnet_ids
}

resource "aws_security_group" "this" {
  name        = "${var.name}-sqlserver"
  description = "AMIE test SQL Server: 1433 from allowed_cidr only"
  vpc_id      = data.aws_vpc.selected.id
}

resource "aws_vpc_security_group_ingress_rule" "sqlserver" {
  security_group_id = aws_security_group.this.id
  description       = "SQL Server from allowed_cidr"
  cidr_ipv4         = var.allowed_cidr
  ip_protocol       = "tcp"
  from_port         = var.port
  to_port           = var.port
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# --- Instance -----------------------------------------------------------------------------------

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = var.engine
  engine_version = local.engine_version
  license_model  = "license-included"
  instance_class = var.instance_class
  port           = var.port

  allocated_storage = var.allocated_storage_gb
  storage_type      = "gp3"
  storage_encrypted = true

  username                    = var.master_username
  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]
  publicly_accessible    = var.publicly_accessible
  option_group_name      = aws_db_option_group.this.name

  # One day of automated backups is the minimum for AWS DMS CDC (transaction-log backups).
  backup_retention_period = 1

  # Test-only teardown settings.
  deletion_protection      = false
  skip_final_snapshot      = true
  delete_automated_backups = true
  apply_immediately        = true

  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true
}
