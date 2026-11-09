variable "region" {
  description = "AWS region. Must match the test SQL Server's region when sqlserver_enabled is true."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for every resource."
  type        = string
  default     = "amie-app"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "Use 3-31 lowercase letters, digits or hyphens, starting with a letter."
  }
}

# --- Network ------------------------------------------------------------------------------------

variable "vpc_id" {
  description = "VPC to deploy into. Empty = the account's default VPC, which is where infra/terraform/rds-sqlserver-test puts the test SQL Server."
  type        = string
  default     = ""
}

variable "private_subnet_cidrs" {
  description = "CIDRs for the two private subnets this stack adds to the VPC. They must be free in the VPC."
  type        = list(string)
  default     = ["172.31.128.0/20", "172.31.144.0/20"]
  validation {
    condition     = length(var.private_subnet_cidrs) == 2
    error_message = "Give exactly two CIDRs."
  }
}

variable "availability_zones" {
  description = "Two AZs for the private subnets. Empty = the first two the region offers, skipping use1-az3, which CloudFront VPC origins do not support."
  type        = list(string)
  default     = []
}

# --- App ----------------------------------------------------------------------------------------

variable "app_username" {
  description = "The one username accepted by the app's sign-in page."
  type        = string
  default     = "amie"
}

variable "app_password" {
  description = "Password for app_username. Empty = generate one (read it with `terraform output -raw app_password`)."
  type        = string
  default     = ""
  sensitive   = true
}

variable "image_tag" {
  description = "Image tag pushed by build-push.sh for all four images."
  type        = string
  default     = "latest"
}

variable "bedrock_model_id" {
  description = "Bedrock model or inference-profile ID for every AI feature."
  type        = string
  default     = "us.anthropic.claude-opus-5-5"
}

variable "task_cpu" {
  description = "Fargate task CPU units (one task runs all four containers)."
  type        = number
  default     = 4096
}

variable "task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 10240
}

variable "origin_read_timeout" {
  description = "Seconds CloudFront waits for the next byte from the ALB. Up to 60 by default; values to 180 need a CloudFront quota increase. SSE streams silent for longer are cut off."
  type        = number
  default     = 60
}

variable "validation_allow_copy" {
  description = "Enable the 'Copy test data to target' button (copies SQL Server rows into Aurora)."
  type        = bool
  default     = true
}

# --- Aurora MySQL (conversion sandbox and validation target) ------------------------------------

variable "aurora_min_acu" {
  description = "Aurora Serverless v2 minimum capacity."
  type        = number
  default     = 0.5
}

variable "aurora_max_acu" {
  description = "Aurora Serverless v2 maximum capacity."
  type        = number
  default     = 4
}

# --- Test SQL Server (infra/terraform/rds-sqlserver-test) ---------------------------------------

variable "sqlserver_enabled" {
  description = "Connect the backend to the test SQL Server from infra/terraform/rds-sqlserver-test (looked up by sqlserver_identifier)."
  type        = bool
  default     = true
}

variable "sqlserver_identifier" {
  description = "RDS identifier of the test SQL Server."
  type        = string
  default     = "amie-wwi-test"
}

variable "sqlserver_database" {
  description = "Database the behaviour tests and validation use."
  type        = string
  default     = "WideWorldImporters"
}
