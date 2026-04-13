output "api_gateway_url" {
  description = "API Gateway invoke URL (Slack Interactivity Request URL: <url>/slack/interact)"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "dynamodb_table_name" {
  description = "DynamoDB table name for approval requests"
  value       = aws_dynamodb_table.approval_requests.name
}

output "lambda_function_name" {
  description = "Lambda function name for the approval webhook"
  value       = aws_lambda_function.approval_webhook.function_name
}
