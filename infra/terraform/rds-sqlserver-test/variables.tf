variable "region" {
  description = "AWS region for the test instance."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for every resource."
  type        = string
  default     = "amie-wwi-test"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,40}$", var.name))
    error_message = "Use 3-41 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "allowed_cidr" {
  description = "The only CIDR allowed to reach port 1433, e.g. your public IP as a.b.c.d/32. Never 0.0.0.0/0."
  type        = string

  validation {
    condition     = can(cidrhost(var.allowed_cidr, 0)) && var.allowed_cidr != "0.0.0.0/0"
    error_message = "allowed_cidr must be a valid CIDR and must not be 0.0.0.0/0."
  }
}

variable "engine" {
  description = "RDS SQL Server edition. sqlserver-ex (Express) is the cheapest; sqlserver-se if Express limits get in the way."
  type        = string
  default     = "sqlserver-ex"

  validation {
    condition     = contains(["sqlserver-ex", "sqlserver-web", "sqlserver-se"], var.engine)
    error_message = "engine must be sqlserver-ex, sqlserver-web or sqlserver-se."
  }
}

variable "instance_class" {
  description = "DB instance class."
  type        = string
  default     = "db.t3.small"
}

variable "allocated_storage_gb" {
  description = "Storage in GB. WideWorldImporters restores to about 3.5 GB."
  type        = number
  default     = 20
}

variable "publicly_accessible" {
  description = "Give the instance a public endpoint so the AMIE backend on a laptop can reach it. Access is still limited to allowed_cidr."
  type        = bool
  default     = true
}

variable "vpc_id" {
  description = "VPC to use. Empty = the account's default VPC."
  type        = string
  default     = ""
}

variable "subnet_ids" {
  description = "Subnets for the DB subnet group (two AZs or more). Empty = every subnet of the VPC."
  type        = list(string)
  default     = []
}

variable "master_username" {
  description = "Master user name. The password is generated and stored by RDS in Secrets Manager."
  type        = string
  default     = "amieadmin"
}

variable "port" {
  description = "SQL Server port. Use a non-default port (e.g. 50000) if your network blocks outbound 1433."
  type        = number
  default     = 1433

  validation {
    condition     = var.port >= 1150 && var.port <= 65535 && !contains([1234, 1434, 3260, 3343, 3389, 47001], var.port) && !(var.port >= 49152 && var.port <= 49156)
    error_message = "RDS for SQL Server accepts 1150-65535 except 1234, 1434, 3260, 3343, 3389, 47001 and 49152-49156."
  }
}
