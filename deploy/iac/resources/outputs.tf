# THE PUBLISHED CONTRACT. These values, and only these, are what the application
# root may consume. It receives them as input variables -- through a pipeline
# variable file, a parameter store read, or a human -- and never by reading this
# root's remote state, which would couple a release to a state file it does not own.
#
# `contract_version` exists so a consumer can assert what it was written against.
# Adding an output is a minor bump; removing or retyping one is a major bump and a
# breaking change for every consumer.

output "contract_version" {
  description = "Semantic version of this durable-output contract"
  value       = "1.0.0"
}

output "kms_key_arn" {
  description = "At-rest key for this service's durable state"
  value       = aws_kms_key.this.arn
}

# COMMS-FLAVOUR-STRIP:BEGIN: outbox relay queue outputs -- no queue for comms services
output "sqs_queue_arn" {
  description = "Outbox relay downstream queue; empty when the service declares no fan-out"
  value       = var.enable_outbox_queue ? aws_sqs_queue.outbox[0].arn : ""
}

output "sqs_queue_url" {
  description = "Outbox relay downstream queue URL; empty when the service declares no fan-out"
  value       = var.enable_outbox_queue ? aws_sqs_queue.outbox[0].url : ""
}
# COMMS-FLAVOUR-STRIP:END

output "artifact_bucket_name" {
  description = "Durable byte store; empty when the service stores no bytes of its own"
  value       = var.enable_artifact_bucket ? aws_s3_bucket.artifacts[0].id : ""
}
