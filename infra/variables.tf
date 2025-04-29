variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "s3_bucket_name" {
  description = "Name of the S3 bucket for storing API discovery reports and temporary data"
  type        = string
  default     = "api-endpoint-discovery-data"
}

variable "dynamodb_table_name" {
  description = "Name of the DynamoDB table for storing API endpoint data"
  type        = string
  default     = "api-endpoints"
}

variable "github_org_name" {
  description = "GitHub organization name to scan repositories"
  type        = string
  default     = "YOUR_ORG"
}

variable "github_pat_secret_name" {
  description = "Name of the AWS Secrets Manager secret for storing GitHub PAT"
  type        = string
  default     = "github-pat-api-discovery"
}

variable "github_pat_value" {
  description = "GitHub Personal Access Token value (sensitive)"
  type        = string
  sensitive   = true
}

variable "bedrock_model_id" {
  description = "AWS Bedrock model ID to use for analysis"
  type        = string
  default     = "anthropic.claude-3-5-sonnet"
}

variable "notification_emails" {
  description = "List of email addresses to receive completion notifications"
  type        = list(string)
  default     = []
}