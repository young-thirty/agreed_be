variable "aws_region" {
  type        = string
  description = "AWS 리전"
  default     = "ap-northeast-2"
}

variable "service_name" {
  type        = string
  description = "App Runner 서비스 이름"
  default     = "agreed-be"
}

variable "ecr_repository_name" {
  type        = string
  description = "ECR repository 이름"
  default     = "agreed-be"
}

variable "github_repository" {
  type        = string
  description = "GitHub 저장소(owner/repo). main 브랜치만 배포 role을 사용할 수 있다."
}

variable "github_branch" {
  type        = string
  description = "배포를 허용할 브랜치"
  default     = "main"
}

variable "frontend_origin" {
  type        = string
  description = "CORS와 OAuth 완료 후 돌아갈 프론트 주소"
}

variable "api_public_url" {
  type        = string
  description = "공개 API 주소. App Runner URL을 넣는다."
}

variable "mongodb_url" {
  type        = string
  sensitive   = true
  description = "MongoDB Atlas connection string"
}

variable "mongodb_db" {
  type        = string
  description = "MongoDB database 이름"
  default     = "agreed"
}

variable "google_client_id" {
  type = string
}

variable "google_redirect_uri" {
  type = string
}

variable "slack_client_id" {
  type = string
}

variable "slack_redirect_uri" {
  type = string
}

variable "demo_session_enabled" {
  type        = bool
  description = "로그인 화면 전 Swagger demo-session. 운영에서는 false 권장."
  default     = false
}

variable "session_cookie_secure" {
  type        = bool
  description = "HTTPS 배포 쿠키"
  default     = true
}

variable "session_cookie_samesite" {
  type        = string
  description = "lax 또는 none. 서로 다른 사이트면 none + secure=true"
  default     = "lax"
  validation {
    condition     = contains(["lax", "strict", "none"], var.session_cookie_samesite)
    error_message = "session_cookie_samesite는 lax, strict, none 중 하나여야 합니다."
  }
}
