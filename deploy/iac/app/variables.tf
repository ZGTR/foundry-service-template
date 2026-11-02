variable "service_name" {
  type    = string
  default = "foundry-service-template"
}

variable "environment" {
  description = "e.g. dev, staging, prod -- each environment owns its own state backend (plan section 8)"
  type        = string
}

variable "aws_region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "image_uri" {
  description = "The ECR image digest (repo@sha256:...) -- the shared seam with iac/serverless.yml. Never a floating tag."
  type        = string

  validation {
    condition     = can(regex("@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "image_uri must be a digest pin (repo@sha256:<64 hex chars>), not a floating tag."
  }
}

variable "exposure" {
  description = "private | edge_iam | public -- must equal this service's row in scripts/service_directory.yaml of the Foundry workspace. It selects nothing here: this root has one ingress, the shared internal listener, for every value. Internet entry for an edge_iam or public operation is the backend API Gateway's, never this root's."
  type        = string

  validation {
    condition     = contains(["private", "edge_iam", "public"], var.exposure)
    error_message = "exposure must be private, edge_iam or public."
  }
}

variable "internal_listener_arn" {
  description = "The shared internal load balancer's HTTPS listener, owned by the estate's internal-edge stack. This service adds one rule to it."
  type        = string
}

variable "internal_alb_sg_id" {
  description = "The shared internal load balancer's security group -- the only source this service's tasks accept on 8080."
  type        = string
}

variable "estate_caller_sg_id" {
  description = "Optional. The internal-edge stack's EstateCallerSecurityGroupId output. Set it when this service CALLS another private service: the shared internal load balancer admits HTTPS from that group, so a caller without it times out. The tasks then carry it in addition to their own group. Empty, the default, changes nothing."
  type        = string
  default     = ""

  validation {
    condition     = var.estate_caller_sg_id == "" || can(regex("^sg-([0-9a-f]{8}|[0-9a-f]{17})$", var.estate_caller_sg_id))
    error_message = "estate_caller_sg_id must be empty or one security group id (sg- and 8 or 17 hex digits), not a name, an ARN or a list."
  }
}

variable "internal_zone_suffix" {
  description = "The private zone services are named in, e.g. svc.internal.example.com. The listener rule matches <service_name>.<internal_zone_suffix>."
  type        = string
}

variable "listener_rule_priority" {
  description = "This service's rule priority on the shared listener, from its row in the service directory. Unique per service: two rules cannot share one."
  type        = number

  validation {
    condition     = var.listener_rule_priority >= 1 && var.listener_rule_priority <= 50000 && floor(var.listener_rule_priority) == var.listener_rule_priority
    error_message = "listener_rule_priority must be a whole number from 1 to 50000, the range a listener rule accepts."
  }
}

variable "ready_path" {
  description = "The readiness route the target group health-checks -- the service directory's health.ready for this service."
  type        = string
}

variable "desired_count" {
  type    = number
  default = 2
}

variable "task_cpu" {
  type    = number
  default = 512
}

variable "task_memory" {
  type    = number
  default = 1024
}

# COMMS-FLAVOUR-STRIP:BEGIN: the outbox relay's downstream queue -- no queue for comms services
variable "sqs_queue_arn" {
  description = "The outbox relay's downstream queue"
  type        = string
}
# COMMS-FLAVOUR-STRIP:END
