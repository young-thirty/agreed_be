# 프론트엔드 연결 안내

## 변경 핵심

Gmail·Slack OAuth와 provider API 호출은 FastAPI로 이관됐습니다. 프론트는
로그인 화면과 연결 버튼, 데이터 표시만 담당합니다.

```dotenv
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

모든 JSON API 호출은 `credentials: 'include'`로 Agreed 세션 쿠키를 보냅니다.
Google access/refresh token과 Slack bot token은 프론트가 받거나 저장하지 않습니다.

## 로그인

```text
POST /api/auth/signup  { name, email, password }
POST /api/auth/login   { email, password }
POST /api/auth/logout
GET  /api/auth/me
```

로그인은 소셜 로그인이 아닙니다. 로그인한 다음 Gmail·Slack을 별도로 연결합니다.

## OAuth 버튼

OAuth 시작은 JSON fetch가 아니라 브라우저 이동입니다.

```ts
window.location.href = apiUrl('/api/email/connect');
window.location.href = apiUrl('/api/slack/connect');
```

성공·실패 후 FastAPI가 프론트 루트로 다음 query와 함께 돌려보냅니다.

```text
/?gmail=connected | failed | denied | login_required
/?slack=connected | failed | denied | login_required
```

Google Cloud Console과 Slack 앱에는 아래 callback을 등록합니다.

```text
http://localhost:8000/api/email/callback
http://localhost:8000/api/slack/callback
```

`localhost:3000/api/email/callback`과
`localhost:3000/api/slack/callback`은 삭제하거나 더 이상 사용하지 않습니다.

## Gmail

```text
GET  /api/email/status
POST /api/email/messages  { maxMessages: 20 }
```

현재 Gmail 권한은 읽기 전용입니다. 응답 초안 생성 기능과 실제 Gmail 발송은
분리하며, 이번 이관에는 발송 endpoint가 없습니다.

## Slack

```text
POST /api/slack/workspaces
POST /api/slack/channels  { teamId }
POST /api/slack/join      { teamId, channelId }
POST /api/slack/messages  { teamId, channelId, oldest? }
POST /api/slack/thread    { teamId, channelId, threadTs, oldest? }
GET  /api/slack/file?teamId=...&fileId=...
```

Slack 메시지 파일에는 provider의 `url_private` 대신 `fileId`만 내려갑니다.
프론트는 위 FastAPI file endpoint로 미리보기/다운로드합니다.

## 프론트에서 제거할 것

- 기존 Next.js `/api/email/*`, `/api/slack/*` 서버 구현
- provider token을 담던 브라우저 cookie/localStorage 처리
- 5초·20초마다 provider를 직접 polling하던 코드

현재 조회 API는 수동 새로고침/화면 진입 시 호출하면 됩니다. 프로젝트별 원문 저장과
자동 수집은 기능 확정서 이후 `SourceMessage`와 worker로 추가합니다.
