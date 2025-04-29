provider "aws" {
  region = var.aws_region
}

# S3 bucket for storing reports and temporary data
resource "aws_s3_bucket" "api_discovery_bucket" {
  bucket = var.s3_bucket_name
  
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "api_discovery_bucket" {
  bucket = aws_s3_bucket.api_discovery_bucket.id
  
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "api_discovery_bucket" {
  bucket = aws_s3_bucket.api_discovery_bucket.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "api_discovery_bucket" {
  bucket                  = aws_s3_bucket.api_discovery_bucket.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# DynamoDB table for tracking API endpoints
resource "aws_dynamodb_table" "api_endpoints" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "repo_path"
  range_key    = "endpoint_id"

  attribute {
    name = "repo_path"
    type = "S"
  }

  attribute {
    name = "endpoint_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }
}

# GitHub PAT secret in Secrets Manager
resource "aws_secretsmanager_secret" "github_pat" {
  name                    = var.github_pat_secret_name
  recovery_window_in_days = 0  # Set to 0 for easy cleanup in development environments
}

resource "aws_secretsmanager_secret_version" "github_pat" {
  secret_id     = aws_secretsmanager_secret.github_pat.id
  secret_string = jsonencode({
    github_pat = var.github_pat_value
  })
}

# SNS Topic for notifications
resource "aws_sns_topic" "api_discovery_notifications" {
  name = "api-endpoint-discovery-notifications"
}

resource "aws_sns_topic_subscription" "email_subscription" {
  count     = length(var.notification_emails)
  topic_arn = aws_sns_topic.api_discovery_notifications.arn
  protocol  = "email"
  endpoint  = var.notification_emails[count.index]
}

# Lambda IAM Role with basic execution permissions
resource "aws_iam_role" "lambda_execution_role" {
  name = "api-discovery-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# CloudWatch logs policy for Lambda
resource "aws_iam_policy" "lambda_logging" {
  name        = "api-discovery-lambda-logging"
  description = "IAM policy for logging from Lambda functions"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Effect   = "Allow"
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_logging.arn
}

# S3 access policy for Lambda
resource "aws_iam_policy" "lambda_s3_access" {
  name        = "api-discovery-lambda-s3-access"
  description = "IAM policy for Lambda to access S3 bucket"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Effect   = "Allow"
        Resource = [
          aws_s3_bucket.api_discovery_bucket.arn,
          "${aws_s3_bucket.api_discovery_bucket.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_s3" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_s3_access.arn
}

# DynamoDB access policy for Lambda
resource "aws_iam_policy" "lambda_dynamodb_access" {
  name        = "api-discovery-lambda-dynamodb-access"
  description = "IAM policy for Lambda to access DynamoDB"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:BatchWriteItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Effect   = "Allow"
        Resource = aws_dynamodb_table.api_endpoints.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_dynamodb" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_dynamodb_access.arn
}

# Secrets Manager access policy for Lambda
resource "aws_iam_policy" "lambda_secretsmanager_access" {
  name        = "api-discovery-lambda-secretsmanager-access"
  description = "IAM policy for Lambda to access Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Effect   = "Allow"
        Resource = aws_secretsmanager_secret.github_pat.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_secretsmanager" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_secretsmanager_access.arn
}

# Bedrock access policy for Lambda
resource "aws_iam_policy" "lambda_bedrock_access" {
  name        = "api-discovery-lambda-bedrock-access"
  description = "IAM policy for Lambda to access AWS Bedrock"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "bedrock:InvokeModel"
        ]
        Effect   = "Allow"
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/anthropic.claude-3-5-sonnet"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_bedrock" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_bedrock_access.arn
}

# SNS access policy for Lambda
resource "aws_iam_policy" "lambda_sns_access" {
  name        = "api-discovery-lambda-sns-access"
  description = "IAM policy for Lambda to publish to SNS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "sns:Publish"
        ]
        Effect   = "Allow"
        Resource = aws_sns_topic.api_discovery_notifications.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_sns" {
  role       = aws_iam_role.lambda_execution_role.name
  policy_arn = aws_iam_policy.lambda_sns_access.arn
}

# Lambda function for listing and cloning repositories
resource "aws_lambda_function" "list_clone" {
  function_name    = "api-discovery-list-clone"
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.10"
  timeout          = 300
  memory_size      = 512
  
  s3_bucket        = aws_s3_bucket.api_discovery_bucket.id
  s3_key           = "lambda/list_clone.zip"
  
  environment {
    variables = {
      S3_BUCKET            = aws_s3_bucket.api_discovery_bucket.id
      GITHUB_PAT_SECRET_ID = aws_secretsmanager_secret.github_pat.name
      GITHUB_ORG           = var.github_org_name
    }
  }

  depends_on = [
    aws_s3_bucket.api_discovery_bucket
  ]
}

# Lambda function for discovering and chunking files
resource "aws_lambda_function" "discover_and_chunk" {
  function_name    = "api-discovery-discover-and-chunk"
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.10"
  timeout          = 300
  memory_size      = 1024
  
  s3_bucket        = aws_s3_bucket.api_discovery_bucket.id
  s3_key           = "lambda/discover_and_chunk.zip"
  
  environment {
    variables = {
      S3_BUCKET = aws_s3_bucket.api_discovery_bucket.id
    }
  }

  depends_on = [
    aws_s3_bucket.api_discovery_bucket
  ]
}

# Lambda function for invoking Bedrock LLM
resource "aws_lambda_function" "invoke_bedrock" {
  function_name    = "api-discovery-invoke-bedrock"
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.10"
  timeout          = 300
  memory_size      = 512
  
  s3_bucket        = aws_s3_bucket.api_discovery_bucket.id
  s3_key           = "lambda/invoke_bedrock.zip"
  
  environment {
    variables = {
      S3_BUCKET        = aws_s3_bucket.api_discovery_bucket.id
      MODEL_ID         = var.bedrock_model_id
    }
  }

  depends_on = [
    aws_s3_bucket.api_discovery_bucket
  ]
}

# Lambda function for aggregating results
resource "aws_lambda_function" "aggregate" {
  function_name    = "api-discovery-aggregate"
  role             = aws_iam_role.lambda_execution_role.arn
  handler          = "lambda_function.lambda_handler"
  runtime          = "python3.10"
  timeout          = 300
  memory_size      = 512
  
  s3_bucket        = aws_s3_bucket.api_discovery_bucket.id
  s3_key           = "lambda/aggregate.zip"
  
  environment {
    variables = {
      S3_BUCKET         = aws_s3_bucket.api_discovery_bucket.id
      DYNAMODB_TABLE    = aws_dynamodb_table.api_endpoints.name
      SNS_TOPIC_ARN     = aws_sns_topic.api_discovery_notifications.arn
    }
  }

  depends_on = [
    aws_s3_bucket.api_discovery_bucket
  ]
}

# IAM role for Step Functions
resource "aws_iam_role" "step_functions_execution_role" {
  name = "api-discovery-step-functions-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
}

# Step Functions Lambda invocation policy
resource "aws_iam_policy" "step_functions_lambda_invocation" {
  name        = "api-discovery-step-functions-lambda-invocation"
  description = "IAM policy for Step Functions to invoke Lambda functions"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "lambda:InvokeFunction"
        ]
        Effect = "Allow"
        Resource = [
          aws_lambda_function.list_clone.arn,
          aws_lambda_function.discover_and_chunk.arn,
          aws_lambda_function.invoke_bedrock.arn,
          aws_lambda_function.aggregate.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "step_functions_lambda_invocation" {
  role       = aws_iam_role.step_functions_execution_role.name
  policy_arn = aws_iam_policy.step_functions_lambda_invocation.arn
}

# Step Function state machine
resource "aws_sfn_state_machine" "api_discovery_workflow" {
  name     = "api-endpoint-discovery-workflow"
  role_arn = aws_iam_role.step_functions_execution_role.arn
  
  definition = templatefile("${path.module}/step_function_definition.json", {
    list_clone_arn         = aws_lambda_function.list_clone.arn,
    discover_and_chunk_arn = aws_lambda_function.discover_and_chunk.arn,
    invoke_bedrock_arn     = aws_lambda_function.invoke_bedrock.arn,
    aggregate_arn          = aws_lambda_function.aggregate.arn
  })
}

# EventBridge rule for scheduling
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "api-discovery-schedule"
  description         = "Schedule for API Endpoint Discovery"
  schedule_expression = "rate(30 days)"
}

resource "aws_cloudwatch_event_target" "api_discovery_target" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  arn       = aws_sfn_state_machine.api_discovery_workflow.arn
  role_arn  = aws_iam_role.eventbridge_role.arn
}

# IAM role for EventBridge to trigger Step Functions
resource "aws_iam_role" "eventbridge_role" {
  name = "api-discovery-eventbridge-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "eventbridge_sfn_execution" {
  name        = "api-discovery-eventbridge-sfn-execution"
  description = "IAM policy for EventBridge to execute Step Functions"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "states:StartExecution"
        ]
        Effect   = "Allow"
        Resource = aws_sfn_state_machine.api_discovery_workflow.arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eventbridge_sfn_execution" {
  role       = aws_iam_role.eventbridge_role.name
  policy_arn = aws_iam_policy.eventbridge_sfn_execution.arn
}