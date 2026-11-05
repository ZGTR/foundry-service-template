variable "service_name" {
  type    = string
  default = "foundry-data-gateway"
}

variable "environment" {
  description = "e.g. dev, staging, prod -- each environment owns its own state backend, and a durable root is never shared across two of them"
  type        = string
}

variable "aws_region" {
  type = string
}

variable "enable_outbox_queue" {
  description = "Whether this service's outbox relay has a downstream queue. A service with no PubSub fan-out sets this false and the app root receives an empty ARN."
  type        = bool
  default     = true
}

variable "enable_artifact_bucket" {
  description = "Whether this service stores durable bytes of its own. Off by default: most services store nothing, and an unused bucket is a durable resource nobody is accountable for."
  type        = bool
  default     = false
}
