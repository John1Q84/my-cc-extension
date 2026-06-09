terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ------------------------------------------------------------------------------
# DynamoDB Table
# ------------------------------------------------------------------------------
#tfsec:ignore:aws-dynamodb-table-customer-key TTL 10분 임시 데이터, CMK 불필요
#tfsec:ignore:aws-dynamodb-enable-recovery TTL 10분 임시 데이터, PITR 불필요
resource "aws_dynamodb_table" "approval_requests" {
  name         = "claude-approval-requests"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "approval_id"

  attribute {
    name = "approval_id"
    type = "S"
  }

  attribute {
    name = "message_ts"
    type = "S"
  }

  global_secondary_index {
    name            = "message_ts-index"
    hash_key        = "message_ts"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled = true # tfsec: aws-dynamodb-enable-at-rest-encryption
  }

  tags = {
    Project = "slack-approval"
  }
}

# ------------------------------------------------------------------------------
# IAM Role for Lambda
# ------------------------------------------------------------------------------
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "lambda_policy" {
  statement {
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.approval_requests.arn,
      "${aws_dynamodb_table.approval_requests.arn}/index/*",
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/claude-approval-webhook:*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "lambda_role" {
  name               = "claude-approval-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  inline_policy {
    name   = "claude-approval-lambda-policy"
    policy = data.aws_iam_policy_document.lambda_policy.json
  }

  tags = {
    Project = "slack-approval"
  }
}

# ------------------------------------------------------------------------------
# Lambda Function
# ------------------------------------------------------------------------------
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../app/lambda_handler.py"
  output_path = "${path.module}/.build/lambda_handler.zip"
}

resource "aws_lambda_function" "approval_webhook" {
  function_name    = "claude-approval-webhook"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_handler.handler"
  runtime          = "python3.12"
  timeout          = 10
  memory_size      = 128
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  tracing_config {
    mode = "Active" # tfsec: aws-lambda-enable-tracing
  }

  environment {
    variables = {
      DYNAMODB_TABLE       = aws_dynamodb_table.approval_requests.name
      SLACK_SIGNING_SECRET = var.slack_signing_secret
    }
  }

  tags = {
    Project = "slack-approval"
  }
}

# ------------------------------------------------------------------------------
# API Gateway HTTP API
# ------------------------------------------------------------------------------
resource "aws_apigatewayv2_api" "approval_api" {
  name          = "claude-approval-api"
  protocol_type = "HTTP"

  tags = {
    Project = "slack-approval"
  }
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                 = aws_apigatewayv2_api.approval_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.approval_webhook.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "slack_interact" {
  api_id    = aws_apigatewayv2_api.approval_api.id
  route_key = "POST /slack/interact"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_apigatewayv2_route" "slack_events" {
  api_id    = aws_apigatewayv2_api.approval_api.id
  route_key = "POST /slack/events"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

#tfsec:ignore:aws-cloudwatch-log-group-customer-key 액세스 로그 전용, AWS 기본 암호화로 충분
resource "aws_cloudwatch_log_group" "api_gateway_logs" {
  name              = "/aws/apigateway/claude-approval-api"
  retention_in_days = 14

  tags = {
    Project = "slack-approval"
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.approval_api.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gateway_logs.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
    })
  }

  tags = {
    Project = "slack-approval"
  }
}

# ------------------------------------------------------------------------------
# Lambda Permission for API Gateway
# ------------------------------------------------------------------------------
resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.approval_webhook.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.approval_api.execution_arn}/*/*"
}
