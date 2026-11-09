# One Fargate task runs all four containers, the same set docker-compose runs. They share the
# task's network namespace, so nginx reaches the others on 127.0.0.1.

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name}"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "this" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  image = { for k, r in aws_ecr_repository.this : k => "${r.repository_url}:${var.image_tag}" }

  log = {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.app.name
      "awslogs-region"        = var.region
      "awslogs-stream-prefix" = "app"
    }
  }

  aurora_secret = aws_rds_cluster.aurora.master_user_secret[0].secret_arn
  app_origin    = "https://${aws_cloudfront_distribution.this.domain_name}"

  health = { interval = 30, timeout = 5, retries = 3, startPeriod = 60 }

  backend_env = concat(
    [
      { name = "AWS_REGION", value = var.region },
      { name = "BEDROCK_MODEL_ID", value = var.bedrock_model_id },
      { name = "AUTH_DISABLED", value = "false" },
      { name = "LOCAL_AUTH_USERNAME", value = var.app_username },
      { name = "AGENTCORE_MODE", value = "false" },
      { name = "CORS_ORIGINS", value = local.app_origin },
      { name = "MYSQL_SANDBOX_HOST", value = aws_rds_cluster.aurora.endpoint },
      { name = "MYSQL_SANDBOX_PORT", value = "3306" },
      { name = "MYSQL_SANDBOX_USER", value = aws_rds_cluster.aurora.master_username },
      { name = "VALIDATION_ALLOW_COPY", value = tostring(var.validation_allow_copy) },
    ],
    var.sqlserver_enabled ? [
      { name = "VALIDATION_SQLSERVER_HOST", value = data.aws_db_instance.sqlserver[0].address },
      { name = "VALIDATION_SQLSERVER_PORT", value = tostring(data.aws_db_instance.sqlserver[0].port) },
      { name = "VALIDATION_SQLSERVER_USER", value = data.aws_db_instance.sqlserver[0].master_username },
      { name = "VALIDATION_SQLSERVER_DATABASE", value = var.sqlserver_database },
    ] : [],
  )

  backend_secrets = concat(
    [
      { name = "LOCAL_AUTH_SECRET", valueFrom = "${aws_secretsmanager_secret.app.arn}:jwt_secret::" },
      { name = "LOCAL_AUTH_PASSWORD", valueFrom = "${aws_secretsmanager_secret.app.arn}:password::" },
      { name = "MYSQL_SANDBOX_PASSWORD", valueFrom = "${local.aurora_secret}:password::" },
    ],
    var.sqlserver_enabled ? [
      {
        name      = "VALIDATION_SQLSERVER_PASSWORD"
        valueFrom = "${data.aws_db_instance.sqlserver[0].master_user_secret[0].secret_arn}:password::"
      },
    ] : [],
  )

  python_health = {
    for port in [8000, 8004, 8005] : tostring(port) => {
      command = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${port}/health')"]
    }
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  ephemeral_storage {
    size_in_gib = 50
  }

  dynamic "volume" {
    for_each = aws_efs_access_point.this
    content {
      name = volume.key
      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.this.id
        transit_encryption = "ENABLED"
        authorization_config {
          access_point_id = volume.value.id
          iam             = "DISABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([
    {
      name         = "frontend"
      image        = local.image["frontend"]
      essential    = true
      portMappings = [{ containerPort = 3000, protocol = "tcp" }]
      environment = [
        # Amazon-provided DNS; upstreams are on localhost inside the task.
        { name = "NGINX_RESOLVER", value = "169.254.169.253" },
        { name = "BACKEND_UPSTREAM", value = "127.0.0.1:8000" },
        { name = "ATX_ANALYSIS_UPSTREAM", value = "127.0.0.1:8004" },
        { name = "ATX_TRANSFORM_UPSTREAM", value = "127.0.0.1:8005" },
      ]
      healthCheck      = merge(local.health, { command = ["CMD-SHELL", "wget -q --spider http://127.0.0.1:3000/ || exit 1"] })
      dependsOn        = [{ containerName = "backend", condition = "HEALTHY" }]
      logConfiguration = merge(local.log, { options = merge(local.log.options, { "awslogs-stream-prefix" = "frontend" }) })
    },
    {
      name             = "backend"
      image            = local.image["backend"]
      essential        = true
      portMappings     = [{ containerPort = 8000, protocol = "tcp" }]
      environment      = local.backend_env
      secrets          = local.backend_secrets
      healthCheck      = merge(local.health, local.python_health["8000"])
      logConfiguration = merge(local.log, { options = merge(local.log.options, { "awslogs-stream-prefix" = "backend" }) })
      mountPoints = [
        { sourceVolume = "backend-temp", containerPath = "/app/temp", readOnly = false },
        { sourceVolume = "shared-repos", containerPath = "/app/shared_repos", readOnly = false },
        { sourceVolume = "transform-defs", containerPath = "/app/shared/transformation_def", readOnly = false },
      ]
    },
    {
      name             = "atx-analysis-agent"
      image            = local.image["atx-analysis-agent"]
      essential        = false
      portMappings     = [{ containerPort = 8004, protocol = "tcp" }]
      environment      = [{ name = "AWS_REGION", value = var.region }, { name = "AGENTCORE_MODE", value = "false" }]
      healthCheck      = merge(local.health, local.python_health["8004"])
      logConfiguration = merge(local.log, { options = merge(local.log.options, { "awslogs-stream-prefix" = "atx-analysis" }) })
      mountPoints      = [{ sourceVolume = "atx-analysis", containerPath = "/app/storage", readOnly = false }]
    },
    {
      name             = "atx-transform-agent"
      image            = local.image["atx-transform-agent"]
      essential        = false
      portMappings     = [{ containerPort = 8005, protocol = "tcp" }]
      environment      = [{ name = "AWS_REGION", value = var.region }, { name = "AGENTCORE_MODE", value = "false" }]
      healthCheck      = merge(local.health, local.python_health["8005"])
      logConfiguration = merge(local.log, { options = merge(local.log.options, { "awslogs-stream-prefix" = "atx-transform" }) })
      mountPoints = [
        { sourceVolume = "atx-transform", containerPath = "/app/storage", readOnly = false },
        { sourceVolume = "transform-defs", containerPath = "/app/shared/transformation_def", readOnly = true },
      ]
    },
  ])
}

resource "aws_ecs_service" "app" {
  name                   = var.name
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.app.arn
  desired_count          = 1
  launch_type            = "FARGATE"
  platform_version       = "LATEST"
  enable_execute_command = true
  # One task holds the EFS-backed state; stop the old one before starting the new one so two
  # never write the same JSON files at once.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  health_check_grace_period_seconds  = 120

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "frontend"
    container_port   = 3000
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http, aws_efs_mount_target.this, aws_rds_cluster_instance.aurora]
}
