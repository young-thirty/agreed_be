# AWS 시연 배포

현재 FastAPI + Docker + MongoDB Atlas + Gmail/Slack OAuth에 가장 작은 운영 구성을
사용한다.

```text
GitHub main
  → GitHub Actions (OIDC)
  → ECR 이미지
  → AWS App Runner (HTTPS URL)
  → MongoDB Atlas
```

App Runner URL이 HTTPS로 바로 발급되므로 별도 EC2 reverse proxy나 인증서 서버를
두지 않는다. App Runner 서비스는 0.25 vCPU / 0.5 GB, 최소 1개 인스턴스로 제한했다.
App Runner는 서울 리전을 지원하지 않으므로 백엔드는 도쿄(`ap-northeast-1`)에 두고,
MongoDB Atlas는 서울 리전을 그대로 사용한다.
시연 종료 후 App Runner 서비스를 삭제하면 실행 비용은 멈추지만, ECR 이미지와
Secrets Manager secret에는 별도 소액 비용이 남을 수 있으므로 함께 정리한다.

## 최초 1회 설정

로컬에 AWS CLI, Docker, Terraform을 설치하고 AWS 자격증명을 설정한다. Terraform
state와 `terraform.tfvars`에는 민감한 값이 들어갈 수 있으므로 절대 Git에 올리지 않는다.

1. `deploy/terraform/terraform.tfvars.example`을 `terraform.tfvars`로 복사한다.
2. MongoDB Atlas URI와 공개 OAuth client ID를 `terraform.tfvars`에 입력한다. DeepSeek,
   Google/Slack secret, `INTEGRATION_TOKEN_KEY`는 로컬 `.env`에 둔다.
3. `api_public_url`, Google/Slack redirect URI는 최초 App Runner URL이 나온 뒤 다시
   채워야 하므로, 첫 실행은 임시 placeholder로 ECR/Secret만 만들고 최종 apply에서
   실제 URL을 넣는다.

실행:

```bash
cp deploy/terraform/terraform.tfvars.example deploy/terraform/terraform.tfvars
# deploy/terraform/terraform.tfvars를 실제 값으로 수정
bash deploy/bootstrap_app_runner.sh
```

스크립트는 다음을 순서대로 한다.

1. ECR과 Secrets Manager secret 이름을 만든다.
2. 현재 Dockerfile을 ECR `latest`로 올린다.
3. 로컬 `.env`의 네 가지 secret을 Secrets Manager에 넣는다.
4. App Runner, HTTPS health check, GitHub OIDC deploy role을 만든다.

`api_public_url`과 OAuth callback은 App Runner URL을 확인한 뒤 Google Cloud Console,
Slack App에 정확히 등록한다. URL이 바뀌면 Terraform 변수와 provider console을 함께
수정한다.

첫 apply에서 출력된 URL을 확인한 뒤 `terraform.tfvars`의 아래 세 값을 실제 URL로
바꾸고 한 번 더 적용한다.

```bash
terraform -chdir=deploy/terraform output -raw apprunner_service_url
terraform -chdir=deploy/terraform apply
```

## GitHub 설정

Terraform output을 확인한다.

```bash
terraform -chdir=deploy/terraform output -raw github_actions_role_arn
terraform -chdir=deploy/terraform output -raw apprunner_service_arn
```

GitHub repository 설정에 추가한다.

- Actions secret `AWS_ROLE_ARN`: `github_actions_role_arn` 출력값
- Actions variable `AWS_REGION`: `ap-northeast-1`
- Actions variable `ECR_REPOSITORY`: `agreed-be`
- Actions variable `APP_RUNNER_SERVICE_ARN`: `apprunner_service_arn` 출력값

이후 `main` push마다 compile/OpenAPI/Docker 검증 후 ECR push → App Runner 배포 →
`/api/health` HTTPS 확인까지 수행한다. GitHub OIDC role은 `main` 브랜치와 지정된
저장소만 허용한다.

## MongoDB Atlas 필수 설정

App Runner에서 Atlas로 나갈 수 있도록 Atlas Network Access에 App Runner의 outbound
주소를 허용해야 한다. 시연만 빠르게 할 때는 Atlas에서 임시로 `0.0.0.0/0`을 허용할 수
있지만, 시연 후에는 고정 egress/VPC peering으로 좁힌다.

배포 환경의 필수 값:

```env
MONGODB_URL=mongodb+srv://...
FRONTEND_ORIGIN=https://<frontend>
GOOGLE_REDIRECT_URI=https://<app-runner-url>/api/email/callback
SLACK_REDIRECT_URI=https://<app-runner-url>/api/slack/callback
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=none
DEMO_SESSION_ENABLED=false
```

프론트가 `localhost:3000`이거나 Vercel 등 다른 사이트에 있으면 API와 사이트가
다르므로 시연에서는 `none`을 사용한다. `none`은 HTTPS와 `secure=true`가 필수다.

`INTEGRATION_TOKEN_KEY`는 로컬에서 이미 연결된 provider token을 계속 사용하려면
현재 `.env`의 값을 그대로 Secrets Manager에 넣어야 한다. 새 값으로 바꾸면 기존
Gmail/Slack 연결을 다시 승인해야 한다.
