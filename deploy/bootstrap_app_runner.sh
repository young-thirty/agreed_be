#!/usr/bin/env bash
set -euo pipefail

# App Runner는 ECR에 이미지가 먼저 있어야 서비스를 만들 수 있다.
# 이 스크립트는 ECR만 만든 뒤 현재 Dockerfile을 bootstrap 이미지로 올리고,
# 마지막 terraform apply에서 App Runner와 GitHub OIDC role을 만든다.

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT_DIR/deploy/terraform"

terraform -chdir="$TF_DIR" init
terraform -chdir="$TF_DIR" apply \
  -target=aws_ecr_repository.api \
  -target=aws_secretsmanager_secret.deepseek_api_key \
  -target=aws_secretsmanager_secret.google_client_secret \
  -target=aws_secretsmanager_secret.slack_client_secret \
  -target=aws_secretsmanager_secret.integration_token_key \
  -auto-approve

REGION="$(terraform -chdir="$TF_DIR" output -raw aws_region)"
REPOSITORY_URL="$(terraform -chdir="$TF_DIR" output -raw ecr_repository_url)"

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REPOSITORY_URL"
docker build -t "$REPOSITORY_URL:latest" "$ROOT_DIR"
docker push "$REPOSITORY_URL:latest"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

: "${DEEPSEEK_API_KEY:?.env에 DEEPSEEK_API_KEY가 필요합니다}"
: "${GOOGLE_CLIENT_SECRET:?.env에 GOOGLE_CLIENT_SECRET가 필요합니다}"
: "${SLACK_CLIENT_SECRET:?.env에 SLACK_CLIENT_SECRET가 필요합니다}"
: "${INTEGRATION_TOKEN_KEY:?.env에 INTEGRATION_TOKEN_KEY가 필요합니다}"

put_secret() {
  local secret_arn="$1"
  local secret_value="$2"
  aws secretsmanager put-secret-value \
    --secret-id "$secret_arn" \
    --secret-string "$secret_value" \
    --region "$REGION" >/dev/null
}

put_secret "$(terraform -chdir="$TF_DIR" output -raw deepseek_secret_arn)" "$DEEPSEEK_API_KEY"
put_secret "$(terraform -chdir="$TF_DIR" output -raw google_secret_arn)" "$GOOGLE_CLIENT_SECRET"
put_secret "$(terraform -chdir="$TF_DIR" output -raw slack_secret_arn)" "$SLACK_CLIENT_SECRET"
put_secret "$(terraform -chdir="$TF_DIR" output -raw integration_key_secret_arn)" "$INTEGRATION_TOKEN_KEY"

terraform -chdir="$TF_DIR" apply -auto-approve

echo "배포 URL: $(terraform -chdir="$TF_DIR" output -raw apprunner_service_url)"
echo "GitHub Actions role: $(terraform -chdir="$TF_DIR" output -raw github_actions_role_arn)"
