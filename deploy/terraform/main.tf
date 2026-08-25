data "aws_caller_identity" "current" {}

data "tls_certificate" "github_actions" {
  url = "https://token.actions.githubusercontent.com/.well-known/openid-configuration"
}

resource "aws_ecr_repository" "api" {
  name                 = var.ecr_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "오래된 이미지 정리"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 8
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_secretsmanager_secret" "deepseek_api_key" {
  name                    = "${var.service_name}/deepseek-api-key"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "google_client_secret" {
  name                    = "${var.service_name}/google-client-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "slack_client_secret" {
  name                    = "${var.service_name}/slack-client-secret"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret" "integration_token_key" {
  name                    = "${var.service_name}/integration-token-key"
  recovery_window_in_days = 0
}

resource "aws_iam_role" "apprunner_ecr_access" {
  name = "${var.service_name}-apprunner-ecr"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "build.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apprunner_ecr_access" {
  role = aws_iam_role.apprunner_ecr_access.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:CompleteLayerUpload",
        "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart",
      ]
      Resource = aws_ecr_repository.api.arn
      }, {
      Effect   = "Allow"
      Action   = "ecr:GetAuthorizationToken"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role" "apprunner_runtime" {
  name = "${var.service_name}-apprunner-runtime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "tasks.apprunner.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apprunner_runtime" {
  role = aws_iam_role.apprunner_runtime.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue", "kms:Decrypt"]
      Resource = [
        aws_secretsmanager_secret.deepseek_api_key.arn,
        aws_secretsmanager_secret.google_client_secret.arn,
        aws_secretsmanager_secret.slack_client_secret.arn,
        aws_secretsmanager_secret.integration_token_key.arn,
      ]
    }]
  })
}

resource "aws_apprunner_auto_scaling_configuration_version" "demo" {
  auto_scaling_configuration_name = "${var.service_name}-demo"
  max_concurrency                 = 80
  max_size                        = 1
  min_size                        = 1
}

resource "aws_apprunner_service" "api" {
  service_name                   = var.service_name
  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.demo.arn

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_ecr_access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.api.repository_url}:latest"
      image_repository_type = "ECR"

      image_configuration {
        port = "8000"
        runtime_environment_variables = {
          FRONTEND_ORIGIN         = var.frontend_origin
          API_PUBLIC_URL          = var.api_public_url
          MONGODB_URL             = var.mongodb_url
          MONGODB_DB              = var.mongodb_db
          GOOGLE_CLIENT_ID        = var.google_client_id
          GOOGLE_REDIRECT_URI     = var.google_redirect_uri
          SLACK_CLIENT_ID         = var.slack_client_id
          SLACK_REDIRECT_URI      = var.slack_redirect_uri
          SESSION_COOKIE_SECURE   = tostring(var.session_cookie_secure)
          SESSION_COOKIE_SAMESITE = var.session_cookie_samesite
          DEMO_SESSION_ENABLED    = tostring(var.demo_session_enabled)
        }
        runtime_environment_secrets = {
          DEEPSEEK_API_KEY      = aws_secretsmanager_secret.deepseek_api_key.arn
          GOOGLE_CLIENT_SECRET  = aws_secretsmanager_secret.google_client_secret.arn
          SLACK_CLIENT_SECRET   = aws_secretsmanager_secret.slack_client_secret.arn
          INTEGRATION_TOKEN_KEY = aws_secretsmanager_secret.integration_token_key.arn
        }
      }
    }
  }

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/api/health"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 5
  }

  instance_configuration {
    cpu               = "0.25 vCPU"
    memory            = "0.5 GB"
    instance_role_arn = aws_iam_role.apprunner_runtime.arn
  }

  depends_on = [aws_iam_role_policy.apprunner_ecr_access, aws_iam_role_policy.apprunner_runtime]

  tags = {
    Project = "agreed"
    Stage   = "demo"
  }
}

# GitHub Actions는 장기 AWS access key를 저장하지 않고 OIDC로 이 role을 AssumeRole한다.
resource "aws_iam_openid_connect_provider" "github_actions" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.github_actions.certificates[0].sha1_fingerprint]
}

resource "aws_iam_role" "github_actions" {
  name = "${var.service_name}-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github_actions.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:${split("/", var.github_repository)[0]}@${var.github_owner_id}/${split("/", var.github_repository)[1]}@${var.github_repository_id}:ref:refs/heads/${var.github_branch}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions" {
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart",
      ]
      Resource = aws_ecr_repository.api.arn
      }, {
      Effect   = "Allow"
      Action   = "ecr:GetAuthorizationToken"
      Resource = "*"
      }, {
      Effect = "Allow"
      Action = [
        "apprunner:DescribeService",
        "apprunner:StartDeployment",
      ]
      Resource = aws_apprunner_service.api.arn
    }]
  })
}
