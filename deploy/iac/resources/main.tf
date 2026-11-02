locals {
  qualified_name = "${var.service_name}-${var.environment}"
}

# --- encryption ------------------------------------------------------------
# The key is durable by definition: rotating a release must never rotate the key
# that the data at rest was encrypted under.

resource "aws_kms_key" "this" {
  description             = "At-rest encryption for ${local.qualified_name} durable state"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "this" {
  name          = "alias/${local.qualified_name}"
  target_key_id = aws_kms_key.this.key_id
}

# COMMS-FLAVOUR-STRIP:BEGIN: the outbox relay's downstream SQS queue -- comms services hold only events:PutEvents on their own bus
# --- outbox relay queue ----------------------------------------------------
# This was ALREADY the one-way contract, informally: the application root took
# it as `var.sqs_queue_arn` from somewhere outside itself. Declaring it here
# makes the owner explicit instead of assumed, and gives the redrive target a
# home that a release cannot delete.

resource "aws_sqs_queue" "outbox_dlq" {
  count                     = var.enable_outbox_queue ? 1 : 0
  name                      = "${local.qualified_name}-outbox-dlq"
  kms_master_key_id         = aws_kms_key.this.id
  message_retention_seconds = 1209600
}

resource "aws_sqs_queue" "outbox" {
  count                     = var.enable_outbox_queue ? 1 : 0
  name                      = "${local.qualified_name}-outbox"
  kms_master_key_id         = aws_kms_key.this.id
  message_retention_seconds = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.outbox_dlq[0].arn
    maxReceiveCount     = 5
  })
}
# COMMS-FLAVOUR-STRIP:END

# --- durable bytes ---------------------------------------------------------
# Off by default. A service that trains models or stores artifacts turns it on;
# the majority that store nothing never create it.

resource "aws_s3_bucket" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = "${local.qualified_name}-artifacts"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  count  = var.enable_artifact_bucket ? 1 : 0
  bucket = aws_s3_bucket.artifacts[0].id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.this.arn
      sse_algorithm     = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  count                   = var.enable_artifact_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
