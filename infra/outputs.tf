output "s3_bucket_name" {
  description = "Name of the S3 bucket created for API discovery data"
  value       = aws_s3_bucket.api_discovery_bucket.id
}

output "dynamodb_table_name" {
  description = "Name of the DynamoDB table created for API endpoints"
  value       = aws_dynamodb_table.api_endpoints.name
}

output "step_function_arn" {
  description = "ARN of the Step Function state machine"
  value       = aws_sfn_state_machine.api_discovery_workflow.arn
}

output "sns_topic_arn" {
  description = "ARN of the SNS topic for notifications"
  value       = aws_sns_topic.api_discovery_notifications.arn
}

output "event_rule_name" {
  description = "Name of the EventBridge rule that schedules the workflow"
  value       = aws_cloudwatch_event_rule.schedule.name
}

output "invoke_manually_command" {
  description = "AWS CLI command to manually trigger the Step Function"
  value       = "aws stepfunctions start-execution --state-machine-arn ${aws_sfn_state_machine.api_discovery_workflow.arn} --region ${var.aws_region}"
}