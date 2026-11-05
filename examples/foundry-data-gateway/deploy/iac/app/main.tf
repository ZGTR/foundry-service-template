# ECS Fargate target: the same ECR image the Lambda target in
# ../serverless.yml deploys. `var.image_uri` is the shared seam (plan
# section 8) -- it must be a digest pin (repo@sha256:...), never a floating
# tag, so promotion between environments is "point at this digest," not a
# rebuild.
#
# Not applied by this template -- see README.md, and nothing here creates
# infrastructure until a service points a real backend at it.
#
# It IS validated, which it previously was not. This header used to say that
# `terraform validate` could not be run in the authoring environment and that
# the file had been hand-checked against the provider's published schema
# instead -- a hand-check that reads exactly like a machine check in the diff.
# `scripts/check_template_iac.sh` now runs `init -backend=false`, `validate`
# and `fmt -check` for real, in a COPY of this directory so the run cannot
# rewrite `.terraform.lock.hcl`, and the same script is the workflow's `iac`
# job. Running it the first time found a genuine defect one file over: the
# workflow's `serverless deploy` could never have worked.

terraform {
  # 1.7 is where `mock_provider` arrived, and tests/estate_caller_sg.tftest.hcl uses it.
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# --- networking: bring your own VPC per environment (plan section 8: "each
# environment names its own account, its own VPC") ------------------------

data "aws_vpc" "this" {
  id = var.vpc_id
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
  tags = {
    Tier = "private"
  }
}

# --- ECS cluster + task + service -----------------------------------------

resource "aws_ecs_cluster" "this" {
  name = "${var.service_name}-${var.environment}"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.service_name}-${var.environment}"
  retention_in_days = 14
}

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.service_name}-${var.environment}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = var.service_name
      image     = var.image_uri # the shared seam with iac/serverless.yml
      essential = true
      portMappings = [
        { containerPort = 8080, protocol = "tcp" }
      ]
      environment = concat([
        { name = "SERVICE_ENV", value = var.environment },
        { name = "PRINCIPAL_ISSUER", value = "foundry-data-gateway" },
        # EVENT_BUS_MODE deliberately absent -- see the note in serverless.yml
        #: no container reads config.event_bus_mode, so setting
        # it declared a durability mode the process cannot honour.
        { name = "PORT", value = "8080" },
      ], local.data_tool_environment) # data_tools.tf
      secrets = local.data_tool_secrets # data_tools.tf: injected from Secrets Manager
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8080/healthz || exit 1"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 10
      }
    }
  ])
}

resource "aws_ecs_service" "this" {
  name            = "${var.service_name}-${var.environment}"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets = data.aws_subnets.private.ids
    # The service's own group, always. The estate's caller group is added only when
    # `estate_caller_sg_id` names it: that group is what the shared internal load
    # balancer admits, so a service that calls another private service must carry it.
    # It is an egress-side membership. Nothing here admits traffic from it.
    # `tests/shape/test_private_ingress.py` compares this expression as it is written:
    # any other group on the tasks is refused, whatever it admits.
    security_groups = concat(
      [aws_security_group.service.id],
      var.estate_caller_sg_id == "" ? [] : [var.estate_caller_sg_id],
    )
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.this.arn
    container_name   = var.service_name
    container_port   = 8080
  }

  # A target group that no listener rule forwards to cannot take a service's tasks.
  depends_on = [aws_lb_listener_rule.this]
}

# --- ingress: one rule on the estate's shared internal load balancer -----------
#
# This root creates NO load balancer and NO listener. The internal-edge stack owns
# one internal ALB, its HTTPS listener, its certificate and the private zone; a
# service adds a target group and a host-header rule, and lets in only that load
# balancer's security group. Callers reach it at `local.service_host` over TLS
# that ends at the shared listener.
#
# It used to create its own: an `aws_lb` with `internal = false`, behind a security
# group that admitted 443 from 0.0.0.0/0. Both are deleted rather than made
# conditional, so there is no input that brings them back.
# `tests/shape/test_private_ingress.py` parses every Terraform file under deploy/iac
# and refuses an internet-facing load balancer, an ingress rule from 0.0.0.0/0 or
# ::/0, and a task with a public IP.

locals {
  service_host = "${var.service_name}.${var.internal_zone_suffix}"
  ingress_tags = { "foundry:exposure" = var.exposure }
}

resource "aws_security_group" "service" {
  name_prefix = "${var.service_name}-${var.environment}-"
  vpc_id      = var.vpc_id
  tags        = local.ingress_tags

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [var.internal_alb_sg_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb_target_group" "this" {
  name        = "${var.service_name}-${var.environment}"
  port        = 8080
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"
  tags        = local.ingress_tags

  health_check {
    path                = var.ready_path # readiness, not liveness -- an ALB should drain, not just detect a dead process
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_listener_rule" "this" {
  listener_arn = var.internal_listener_arn
  priority     = var.listener_rule_priority

  condition {
    host_header {
      values = [local.service_host]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

# --- IAM --------------------------------------------------------------

data "aws_iam_policy_document" "assume_ecs_tasks" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.service_name}-${var.environment}-execution"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task" {
  name               = "${var.service_name}-${var.environment}-task"
  assume_role_policy = data.aws_iam_policy_document.assume_ecs_tasks.json
}

# COMMS-FLAVOUR-STRIP:BEGIN: sqs:SendMessage IAM -- comms services hold only events:PutEvents on their own bus
resource "aws_iam_role_policy" "task_sqs" {
  name = "outbox-publish"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.sqs_queue_arn
      }
    ]
  })
}
# COMMS-FLAVOUR-STRIP:END
