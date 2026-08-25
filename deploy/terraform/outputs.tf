output "aws_region" {
  value = var.aws_region
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "apprunner_service_arn" {
  value = aws_apprunner_service.api.arn
}

output "apprunner_service_url" {
  value = "https://${aws_apprunner_service.api.service_url}"
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "deepseek_secret_arn" {
  value = aws_secretsmanager_secret.deepseek_api_key.arn
}

output "google_secret_arn" {
  value = aws_secretsmanager_secret.google_client_secret.arn
}

output "slack_secret_arn" {
  value = aws_secretsmanager_secret.slack_client_secret.arn
}

output "integration_key_secret_arn" {
  value = aws_secretsmanager_secret.integration_token_key.arn
}
