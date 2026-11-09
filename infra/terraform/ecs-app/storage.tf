# --- ECR ----------------------------------------------------------------------------------------

locals {
  images = ["frontend", "backend", "atx-analysis-agent", "atx-transform-agent"]
}

resource "aws_ecr_repository" "this" {
  for_each             = toset(local.images)
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# --- EFS: everything the app keeps under /app/temp and /app/storage ------------------------------

resource "aws_efs_file_system" "this" {
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  tags             = { Name = "${var.name}-data" }
}

resource "aws_efs_mount_target" "this" {
  count           = 2
  file_system_id  = aws_efs_file_system.this.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

locals {
  # One access point per container path; every container runs as uid/gid 1000 (appuser).
  efs_paths = {
    backend-temp   = "/backend-temp"
    shared-repos   = "/shared-repos"
    transform-defs = "/transformation-definitions"
    atx-analysis   = "/atx-analysis-storage"
    atx-transform  = "/atx-transform-storage"
  }
}

resource "aws_efs_access_point" "this" {
  for_each       = local.efs_paths
  file_system_id = aws_efs_file_system.this.id
  posix_user {
    uid = 1000
    gid = 1000
  }
  root_directory {
    path = each.value
    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0755"
    }
  }
  tags = { Name = "${var.name}-${each.key}" }
}

# --- Aurora MySQL 3 (Serverless v2): conversion sandbox + validation target ---------------------

data "aws_rds_engine_version" "aurora" {
  engine = "aurora-mysql"
  latest = true
  filter {
    name   = "engine-mode"
    values = ["provisioned"]
  }
  # Aurora MySQL 3 = MySQL 8.0 compatible, what the conversions target.
  version = "8.0"
}

resource "aws_db_subnet_group" "aurora" {
  name       = "${var.name}-aurora"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_rds_cluster_parameter_group" "aurora" {
  name        = "${var.name}-aurora-mysql8"
  family      = "aurora-mysql8.0"
  description = "AMIE sandbox: same settings as the local mysql:8.0 container"

  # Converted functions are created without DETERMINISTIC/READS SQL DATA declarations.
  parameter {
    name  = "log_bin_trust_function_creators"
    value = "1"
  }
  parameter {
    name  = "sql_mode"
    value = "STRICT_TRANS_TABLES,NO_ENGINE_SUBSTITUTION"
  }
}

resource "aws_rds_cluster" "aurora" {
  cluster_identifier              = "${var.name}-aurora"
  engine                          = "aurora-mysql"
  engine_version                  = data.aws_rds_engine_version.aurora.version_actual
  engine_mode                     = "provisioned"
  master_username                 = "amieadmin"
  manage_master_user_password     = true
  db_subnet_group_name            = aws_db_subnet_group.aurora.name
  vpc_security_group_ids          = [aws_security_group.aurora.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.aurora.name
  storage_encrypted               = true

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_acu
    max_capacity = var.aurora_max_acu
  }

  backup_retention_period = 1
  # Demo stack: removable without a snapshot.
  deletion_protection = false
  skip_final_snapshot = true
  apply_immediately   = true
}

resource "aws_rds_cluster_instance" "aurora" {
  identifier          = "${var.name}-aurora-1"
  cluster_identifier  = aws_rds_cluster.aurora.id
  instance_class      = "db.serverless"
  engine              = aws_rds_cluster.aurora.engine
  engine_version      = aws_rds_cluster.aurora.engine_version
  publicly_accessible = false
}

# --- Secrets ------------------------------------------------------------------------------------

resource "random_password" "app" {
  length  = 20
  special = false
}

resource "random_password" "jwt" {
  length  = 48
  special = false
}

locals {
  app_password = var.app_password != "" ? var.app_password : random_password.app.result
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/app-auth"
  description             = "AMIE sign-in password and JWT signing key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    password   = local.app_password
    jwt_secret = random_password.jwt.result
  })
}
