"""
Terraform template generator for ECS Fargate.
Uses Jinja2 templates instead of AI generation to avoid rate limits.
"""

from typing import Dict
from src.agentcore.models import RepositoryContext


def generate_fargate_terraform(
    context: RepositoryContext, project_id: str, repo_full_name: str = None
) -> Dict[str, str]:
    """
    Generate complete Terraform configuration for ECS Fargate using templates.
    No AI calls needed - instant generation.
    """
    
    app_port = context.ports[0] if context.ports else 3000
    health_path = context.health_check_path or "/health"
    
    # Extract repository name from full name or use project_id
    if repo_full_name:
        repo_name = repo_full_name.split("/")[-1].lower().replace("_", "-")
    else:
        # Fallback to project_id if repo name not provided
        repo_name = f"app-{project_id[:8]}"
    
    # Use repo_name as the default app_name to avoid "myapp" everywhere
    app_name = repo_name
    
    files = {}
    
    # main.tf
    files["main.tf"] = f"""terraform {{
  required_version = ">= 1.9.0"
  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = var.region
}}

# VPC
resource "aws_vpc" "main" {{
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = {{
    Name        = "${{var.app_name}}-vpc"
    Environment = var.environment
  }}
}}

# Public Subnets (for ALB)
resource "aws_subnet" "public" {{
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.public_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = {{
    Name        = "${{var.app_name}}-public-${{count.index + 1}}"
    Environment = var.environment
  }}
}}

# Private Subnets (for ECS tasks)
resource "aws_subnet" "private" {{
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = {{
    Name        = "${{var.app_name}}-private-${{count.index + 1}}"
    Environment = var.environment
  }}
}}

# Internet Gateway
resource "aws_internet_gateway" "main" {{
  vpc_id = aws_vpc.main.id
  tags = {{
    Name        = "${{var.app_name}}-igw"
    Environment = var.environment
  }}
}}

# NAT Gateway
resource "aws_eip" "nat" {{
  domain = "vpc"
  tags = {{
    Name        = "${{var.app_name}}-nat-eip"
    Environment = var.environment
  }}
}}

resource "aws_nat_gateway" "main" {{
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags = {{
    Name        = "${{var.app_name}}-nat"
    Environment = var.environment
  }}
  depends_on = [aws_internet_gateway.main]
}}

# Route Tables
resource "aws_route_table" "public" {{
  vpc_id = aws_vpc.main.id
  route {{
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }}
  tags = {{
    Name        = "${{var.app_name}}-public-rt"
    Environment = var.environment
  }}
}}

resource "aws_route_table" "private" {{
  vpc_id = aws_vpc.main.id
  route {{
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }}
  tags = {{
    Name        = "${{var.app_name}}-private-rt"
    Environment = var.environment
  }}
}}

resource "aws_route_table_association" "public" {{
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}}

resource "aws_route_table_association" "private" {{
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}}

# ECR Repository (must exist before deployment)
# Created during Docker build step
data "aws_ecr_repository" "main" {{
  name = var.ecr_repository_name
}}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "main" {{
  name              = "/ecs/${{var.app_name}}"
  retention_in_days = 7
  tags = {{
    Name        = "${{var.app_name}}-logs"
    Environment = var.environment
  }}
}}

# ECS Cluster
resource "aws_ecs_cluster" "main" {{
  name = "${{var.app_name}}-cluster"
  setting {{
    name  = "containerInsights"
    value = "enabled"
  }}
  tags = {{
    Name        = "${{var.app_name}}-cluster"
    Environment = var.environment
  }}
}}

# ECS Task Definition
resource "aws_ecs_task_definition" "app" {{
  family                   = "${{var.app_name}}-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.container_cpu
  memory                   = var.container_memory
  execution_role_arn       = aws_iam_role.ecs_execution_role.arn
  task_role_arn            = aws_iam_role.ecs_task_role.arn

  container_definitions = jsonencode([
    {{
      name  = var.app_name
      image = "${{data.aws_ecr_repository.main.repository_url}}:latest"
      portMappings = [
        {{
          containerPort = {app_port}
          hostPort      = {app_port}
          protocol      = "tcp"
        }}
      ]
      # Environment variables from terraform.tfvars
      environment = [
        for key, value in var.app_env_vars != null ? var.app_env_vars : {{}} : {{
          name  = key
          value = tostring(value)
        }}
      ]
      logConfiguration = {{
        logDriver = "awslogs"
        options = {{
          "awslogs-group"         = aws_cloudwatch_log_group.main.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "ecs"
        }}
      }}
      healthCheck = {{
        command     = ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:{app_port}{health_path} || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 60
      }}
    }}
  ])
  tags = {{
    Name        = "${{var.app_name}}-task"
    Environment = var.environment
  }}
}}

# ECS Service
resource "aws_ecs_service" "main" {{
  name            = "${{var.app_name}}-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  launch_type     = "FARGATE"
  desired_count   = var.desired_count

  network_configuration {{
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }}

  load_balancer {{
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = var.app_name
    container_port   = {app_port}
  }}

  deployment_circuit_breaker {{
    enable   = true
    rollback = true
  }}

  tags = {{
    Name        = "${{var.app_name}}-service"
    Environment = var.environment
  }}

  depends_on = [aws_lb_listener.http]
}}

# Application Load Balancer
resource "aws_lb" "main" {{
  name               = "${{var.app_name}}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  enable_deletion_protection = false
  tags = {{
    Name        = "${{var.app_name}}-alb"
    Environment = var.environment
  }}
}}

# Target Group
resource "aws_lb_target_group" "app" {{
  name        = "${{var.app_name}}-tg"
  port        = {app_port}
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {{
    enabled             = true
    interval            = 30
    path                = "/"
    port                = "traffic-port"
    healthy_threshold   = 2
    unhealthy_threshold = 10
    timeout             = 10
    protocol            = "HTTP"
    matcher             = "200-499"
  }}

  deregistration_delay = 30

  tags = {{
    Name        = "${{var.app_name}}-tg"
    Environment = var.environment
  }}
}}

# ALB Listener - HTTPS (conditional on enable_https)
resource "aws_lb_listener" "https" {{
  count             = var.enable_https ? 1 : 0
  load_balancer_arn = aws_lb.main.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS-1-2-2017-01"
  certificate_arn   = var.acm_certificate_arn

  default_action {{
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }}
}}

# ALB Listener - HTTP (redirect to HTTPS if enabled, otherwise forward)
resource "aws_lb_listener" "http" {{
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {{
    type = var.enable_https ? "redirect" : "forward"
    
    dynamic "redirect" {{
      for_each = var.enable_https ? [1] : []
      content {{
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }}
    }}
    
    target_group_arn = var.enable_https ? null : aws_lb_target_group.app.arn
  }}
}}
"""

    # variables.tf
    files["variables.tf"] = f'''variable "region" {{
  description = "AWS region for resources"
  type        = string
  default     = "us-west-2"
}}

variable "app_name" {{
  description = "Application name (must match Docker image name used in build)"
  type        = string
}}

variable "ecr_repository_name" {{
  description = "ECR repository name (must match the repository created during Docker build)"
  type        = string
}}

variable "environment" {{
  description = "Environment (dev, staging, prod)"
  type        = string
  default     = "production"
}}

variable "vpc_cidr" {{
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}}

variable "public_subnet_cidrs" {{
  description = "CIDR blocks for public subnets"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}}

variable "private_subnet_cidrs" {{
  description = "CIDR blocks for private subnets"
  type        = list(string)
  default     = ["10.0.11.0/24", "10.0.12.0/24"]
}}

variable "container_cpu" {{
  description = "CPU units for container (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}}

variable "container_memory" {{
  description = "Memory for container in MB"
  type        = number
  default     = 512
}}

variable "desired_count" {{
  description = "Desired number of ECS tasks"
  type        = number
  default     = 2
}}

variable "acm_certificate_arn" {{
  description = "ARN of ACM certificate for HTTPS listener. Set enable_https=true and provide valid ARN to enable HTTPS"
  type        = string
  default     = ""
}}

variable "enable_https" {{
  description = "Enable HTTPS listener (requires valid ACM certificate)"
  type        = bool
  default     = false
}}

variable "app_env_vars" {{
  description = "Application environment variables"
  type        = map(string)
  default     = {{}}
}}
'''

    # outputs.tf
    files["outputs.tf"] = """output "alb_dns_name" {
  description = "ALB DNS name - use this to access your application"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL - Docker image location"
  value       = data.aws_ecr_repository.main.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.main.name
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for application logs"
  value       = aws_cloudwatch_log_group.main.name
}
"""

    # iam.tf
    files["iam.tf"] = """# ECS Task Execution Role (for ECS to pull images and write logs)
resource "aws_iam_role" "ecs_execution_role" {
  name = "${var.app_name}-ecs-execution-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
  tags = {
    Name        = "${var.app_name}-ecs-execution-role"
    Environment = var.environment
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_role_policy" {
  role       = aws_iam_role.ecs_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ECS Task Role (for application to access AWS services)
resource "aws_iam_role" "ecs_task_role" {
  name = "${var.app_name}-ecs-task-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
    }]
  })
  tags = {
    Name        = "${var.app_name}-ecs-task-role"
    Environment = var.environment
  }
}

# Add custom policies to ecs_task_role as needed for your application
# Example: S3 access, DynamoDB access, etc.
"""

    # security_groups.tf
    files["security_groups.tf"] = f"""# ALB Security Group
resource "aws_security_group" "alb" {{
  name        = "${{var.app_name}}-alb-sg"
  description = "Security group for Application Load Balancer"
  vpc_id      = aws_vpc.main.id

  ingress {{
    description = "HTTP from internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }}

  ingress {{
    description = "HTTPS from internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }}

  egress {{
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}

  tags = {{
    Name        = "${{var.app_name}}-alb-sg"
    Environment = var.environment
  }}
}}

# ECS Tasks Security Group
resource "aws_security_group" "ecs_tasks" {{
  name        = "${{var.app_name}}-ecs-tasks-sg"
  description = "Security group for ECS tasks"
  vpc_id      = aws_vpc.main.id

  ingress {{
    description     = "Allow traffic from ALB"
    from_port       = {app_port}
    to_port         = {app_port}
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }}

  egress {{
    description = "Allow all outbound (for pulling images, etc.)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}

  tags = {{
    Name        = "${{var.app_name}}-ecs-tasks-sg"
    Environment = var.environment
  }}
}}
"""

    # data.tf
    files["data.tf"] = """# Get available availability zones
data "aws_availability_zones" "available" {
  state = "available"
}

# Get current AWS account ID
data "aws_caller_identity" "current" {}
"""

    # backend.tf
    from src.agentcore.templates.terraform_backend import generate_backend_config

    files["backend.tf"] = generate_backend_config(project_id)
    
    return files
