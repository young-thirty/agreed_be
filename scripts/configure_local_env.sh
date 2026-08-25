#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
ENV_EXAMPLE="$REPO_DIR/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$ENV_EXAMPLE" "$ENV_FILE"
fi

replace_env_value() {
  local env_key="$1"
  local env_value="$2"
  local temp_file
  local found=false

  temp_file="$(mktemp "${ENV_FILE}.XXXXXX")"
  while IFS= read -r env_line || [[ -n "$env_line" ]]; do
    if [[ "$env_line" == "$env_key="* ]]; then
      printf '%s=%s\n' "$env_key" "$env_value" >> "$temp_file"
      found=true
    else
      printf '%s\n' "$env_line" >> "$temp_file"
    fi
  done < "$ENV_FILE"

  if [[ "$found" == false ]]; then
    printf '%s=%s\n' "$env_key" "$env_value" >> "$temp_file"
  fi

  mv "$temp_file" "$ENV_FILE"
}

read -r -s -p "GOOGLE_CLIENT_SECRET 붙여넣기: " google_client_secret
printf '\n'
read -r -s -p "SLACK_CLIENT_SECRET 붙여넣기: " slack_client_secret
printf '\n'
read -r -s -p "DEEPSEEK_API_KEY 붙여넣기 (없으면 Enter): " deepseek_api_key
printf '\n'

if [[ -z "$google_client_secret" || -z "$slack_client_secret" ]]; then
  printf 'Google과 Slack client secret은 비워둘 수 없습니다. 다시 실행해 주세요.\n' >&2
  exit 1
fi

integration_token_key=""
while IFS= read -r env_line || [[ -n "$env_line" ]]; do
  if [[ "$env_line" == "INTEGRATION_TOKEN_KEY="* ]]; then
    integration_token_key="${env_line#INTEGRATION_TOKEN_KEY=}"
    break
  fi
done < "$ENV_FILE"

if [[ -z "$integration_token_key" ]]; then
  integration_token_key="$(python3 -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
fi

replace_env_value "GOOGLE_CLIENT_ID" "256892949106-2mr4ptdl92i9kh2l48c6fr9dbcdrihdr.apps.googleusercontent.com"
replace_env_value "GOOGLE_CLIENT_SECRET" "$google_client_secret"
replace_env_value "GOOGLE_REDIRECT_URI" "http://localhost:8000/api/email/callback"
replace_env_value "SLACK_CLIENT_ID" "11918653874000.11895857626291"
replace_env_value "SLACK_CLIENT_SECRET" "$slack_client_secret"
replace_env_value "SLACK_REDIRECT_URI" "http://localhost:8000/api/slack/callback"
replace_env_value "INTEGRATION_TOKEN_KEY" "$integration_token_key"

if [[ -n "$deepseek_api_key" ]]; then
  replace_env_value "DEEPSEEK_API_KEY" "$deepseek_api_key"
fi

chmod 600 "$ENV_FILE"
unset google_client_secret slack_client_secret deepseek_api_key integration_token_key

printf '완료: %s (권한 600)\n' "$ENV_FILE"
printf 'Google/Slack 콘솔의 redirect URI도 localhost:8000 callback으로 맞춰 주세요.\n'
