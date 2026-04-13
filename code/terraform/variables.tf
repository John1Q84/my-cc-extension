variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "ap-northeast-2"
}

variable "slack_signing_secret" {
  description = "Slack app signing secret for verifying webhook requests"
  type        = string
  sensitive   = true
}
