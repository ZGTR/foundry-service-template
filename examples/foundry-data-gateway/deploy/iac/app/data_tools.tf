# Data tools: the catalogue (tool packs + tenant policy) and the secrets the gateway reads.
#
# * Packs and policy are versioned S3 objects uploaded from this repository, so a reviewed
#   change to packs/*.tools.yaml is a new object version; the service reads them at
#   startup through TOOL_CATALOGUE_URI (adapters/real/yaml_catalogue.py, S3CatalogueLoader).
# * Credentials never live in a pack: DATA_SOURCE_URLS (JSON: connection name -> read-only
#   DSN) and the foundry-agent webhook key are Secrets Manager secrets the CALLER creates
#   and owns; this module only grants the execution role permission to inject them.
# * The task role may read the catalogue prefix and nothing else in the bucket.

variable "tool_packs_dir" {
  description = "Directory holding the *.tools.yaml packs to publish (relative to this module)."
  type        = string
  default     = "../../../packs"
}

variable "tool_policy_file" {
  description = "This installation's tenant policy (foundry.dev/tool-policy/v1)."
  type        = string
}

variable "data_source_urls_secret_arn" {
  description = "Secrets Manager secret whose value is the DATA_SOURCE_URLS JSON object."
  type        = string
}

variable "tool_webhook_secret_arn" {
  description = "Secrets Manager secret holding the HMAC key foundry-agent signs record-tool calls with."
  type        = string
}

variable "agent_model_id" {
  description = "Model id written into the generated foundry-agent definition."
  type        = string
  default     = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
}

locals {
  catalogue_prefix = "catalogue/"
  pack_files       = fileset(var.tool_packs_dir, "*.tools.yaml")

  data_tool_environment = [
    { name = "TOOL_CATALOGUE_URI", value = "s3://${aws_s3_bucket.catalogue.bucket}/${local.catalogue_prefix}" },
    { name = "AGENT_MODEL_ID", value = var.agent_model_id },
    { name = "AWS_REGION", value = var.aws_region },
  ]
  data_tool_secrets = [
    { name = "DATA_SOURCE_URLS", valueFrom = var.data_source_urls_secret_arn },
    { name = "TOOL_WEBHOOK_SECRET", valueFrom = var.tool_webhook_secret_arn },
  ]
}

resource "aws_s3_bucket" "catalogue" {
  bucket_prefix = "${var.service_name}-${var.environment}-catalogue-"
}

resource "aws_s3_bucket_versioning" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "catalogue" {
  bucket                  = aws_s3_bucket.catalogue.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "catalogue" {
  bucket = aws_s3_bucket.catalogue.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_object" "pack" {
  for_each     = local.pack_files
  bucket       = aws_s3_bucket.catalogue.id
  key          = "${local.catalogue_prefix}${each.value}"
  source       = "${var.tool_packs_dir}/${each.value}"
  etag         = filemd5("${var.tool_packs_dir}/${each.value}")
  content_type = "application/yaml"
}

resource "aws_s3_object" "policy" {
  bucket       = aws_s3_bucket.catalogue.id
  key          = "${local.catalogue_prefix}policy.yaml"
  source       = var.tool_policy_file
  etag         = filemd5(var.tool_policy_file)
  content_type = "application/yaml"
}

resource "aws_iam_role_policy" "task_catalogue_read" {
  name = "data-tool-catalogue-read"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.catalogue.arn}/${local.catalogue_prefix}*"
      },
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.catalogue.arn
        Condition = { StringLike = { "s3:prefix" = ["${local.catalogue_prefix}*"] } }
      },
    ]
  })
}

resource "aws_iam_role_policy" "execution_data_tool_secrets" {
  name = "data-tool-secrets"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.data_source_urls_secret_arn, var.tool_webhook_secret_arn]
    }]
  })
}

output "tool_catalogue_uri" {
  value = "s3://${aws_s3_bucket.catalogue.bucket}/${local.catalogue_prefix}"
}
