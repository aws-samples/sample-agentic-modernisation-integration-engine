# Private subnets and a NAT gateway added to an existing VPC (the default VPC unless vpc_id is
# set), so the app shares a network with the test SQL Server and reaches it privately.

data "aws_vpc" "this" {
  default = var.vpc_id == ""
  id      = var.vpc_id == "" ? null : var.vpc_id
}

data "aws_availability_zones" "available" {
  state = "available"
  # CloudFront VPC origins cannot attach to use1-az3.
  exclude_zone_ids = ["use1-az3"]
}

data "aws_internet_gateway" "this" {
  filter {
    name   = "attachment.vpc-id"
    values = [data.aws_vpc.this.id]
  }
}

locals {
  azs = length(var.availability_zones) > 0 ? var.availability_zones : slice(data.aws_availability_zones.available.names, 0, 2)
}

# A public subnet for the NAT gateway: the VPC's subnet in the first AZ that maps public IPs.
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.this.id]
  }
  filter {
    name   = "availability-zone"
    values = [local.azs[0]]
  }
  filter {
    name   = "map-public-ip-on-launch"
    values = ["true"]
  }
}

resource "aws_subnet" "private" {
  count                   = 2
  vpc_id                  = data.aws_vpc.this.id
  cidr_block              = var.private_subnet_cidrs[count.index]
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name}-private-${local.azs[count.index]}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${var.name}-nat" }
}

resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = data.aws_subnets.public.ids[0]
  tags          = { Name = "${var.name}-nat" }
  depends_on    = [data.aws_internet_gateway.this]
}

resource "aws_route_table" "private" {
  vpc_id = data.aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = { Name = "${var.name}-private" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- Security groups ----------------------------------------------------------------------------

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb"
  description = "AMIE internal ALB: HTTP from CloudFront origin-facing servers only"
  vpc_id      = data.aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from CloudFront (VPC origin)"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the app task"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}

resource "aws_security_group" "task" {
  name        = "${var.name}-task"
  description = "AMIE ECS task: port 3000 from the ALB only"
  vpc_id      = data.aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  description                  = "nginx from the ALB"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}

resource "aws_vpc_security_group_egress_rule" "task_all" {
  security_group_id = aws_security_group.task.id
  description       = "Bedrock, ECR, Secrets Manager, GitHub, databases, EFS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "efs" {
  name        = "${var.name}-efs"
  description = "AMIE EFS: NFS from the app task only"
  vpc_id      = data.aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_task" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS from the app task"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
}

resource "aws_security_group" "aurora" {
  name        = "${var.name}-aurora"
  description = "AMIE Aurora MySQL: 3306 from the app task only"
  vpc_id      = data.aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "aurora_from_task" {
  security_group_id            = aws_security_group.aurora.id
  description                  = "MySQL from the app task"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}

# --- Test SQL Server: let the task in -----------------------------------------------------------

data "aws_db_instance" "sqlserver" {
  count                  = var.sqlserver_enabled ? 1 : 0
  db_instance_identifier = var.sqlserver_identifier
}

resource "aws_vpc_security_group_ingress_rule" "sqlserver_from_task" {
  count                        = var.sqlserver_enabled ? 1 : 0
  security_group_id            = one(data.aws_db_instance.sqlserver[0].vpc_security_groups)
  description                  = "SQL Server from the AMIE app task"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = data.aws_db_instance.sqlserver[0].port
  to_port                      = data.aws_db_instance.sqlserver[0].port
}
