# Agreed 백엔드 인수인계

## 제품 원칙

Agreed는 계약 체결 이후 프리랜서와 클라이언트가 Gmail·Slack에서 주고받는
대화에서 새 요구사항을 찾고 계약 변경분을 관리합니다.

> AI는 무엇이 바뀌었는지 정리하고, 사람은 받아줄지·금액·납기를 결정한다.

AI는 `합의`, `완료`, `거절`을 제안하지 않습니다. 계약은 사람이 합의 상태를
확정한 뒤 `POST /api/contract/apply` 한 경로로만 변경합니다.

## 저장소 경계

| 저장소 | 담당 |
|---|---|
| `young-thirty/agreed_dev` | Next.js 화면과 FastAPI 호출 |
| `young-thirty/agreed_be` | 로그인, MongoDB, AI, Gmail·Slack OAuth/API |

프론트에는 공개 가능한 API 주소만 둡니다. DeepSeek·Google·Slack secret과
provider token은 백엔드에서만 다룹니다.

## 현재 구현

- 자체 회원가입·로그인·로그아웃·현재 사용자 조회
- opaque session을 HttpOnly cookie로 발급하고 DB에는 hash만 저장
- Contract·Requirement·IntegrationConnection을 로그인 사용자에게 귀속
- Google authorization-code OAuth, refresh token 보관, Gmail 읽기
- Slack OAuth, 워크스페이스·채널·메시지·스레드·파일 읽기
- provider token을 Fernet으로 암호화해 MongoDB 저장
- OAuth callback을 시작 로그인 세션과 연결
- 계약 diff와 합의 후 멱등 반영
- DeepSeek 실패 시 고정 시연 폴백
- Project/SourceMessage/ClientRequest/ProjectMaterial/AnalysisRun 저장
- 프로젝트 목록·상세·요청·자료·소유권 API
- 프로젝트별 계약·요구사항 조회/전이/apply
- 프로젝트 Gmail 상대·Slack 채널 source-link 등록과 sync
- 원문 저장 후 BackgroundTasks 요청 3색 판정·근거·자료 종류 분류

Gmail 권한은 현재 `gmail.readonly`뿐입니다. 화면의 답장은 “초안 생성”까지이며
실제 메일 전송 API는 아직 만들지 않았습니다.

## OAuth callback

Google Cloud Console과 Slack 앱 설정에서 아래 값을 정확히 등록합니다.

```text
Google: http://localhost:8000/api/email/callback
Slack:  http://localhost:8000/api/slack/callback
```

예전 `localhost:3000/api/.../callback`은 Next.js 서버 구현의 주소이므로 더 이상
사용하지 않습니다. 운영 배포 후에는 같은 path의 HTTPS 백엔드 주소를 추가합니다.

환경설정은 `bash scripts/configure_local_env.sh`로 입력합니다. `.env`는
gitignore 대상이며 파일 권한은 600으로 맞춥니다.

## 주요 API

| 메서드 | 경로 | 역할 |
|---|---|---|
| POST | `/api/auth/signup` | 자체 회원가입 + 로그인 |
| POST | `/api/auth/login` | 자체 로그인 |
| POST | `/api/auth/logout` | 로그아웃 |
| GET | `/api/auth/me` | 현재 사용자 |
| GET | `/api/email/status` | Gmail 연결 상태 |
| GET | `/api/email/connect` | Gmail OAuth 시작 |
| GET | `/api/email/callback` | Google callback |
| POST | `/api/email/messages` | 최근 Gmail 조회·그룹화 |
| GET | `/api/slack/connect` | Slack OAuth 시작 |
| GET | `/api/slack/callback` | Slack callback |
| POST | `/api/slack/workspaces` | 연결 워크스페이스 |
| POST | `/api/slack/channels` | 채널 목록 |
| POST | `/api/slack/join` | 공개 채널 참여 |
| POST | `/api/slack/messages` | 채널 메시지 |
| POST | `/api/slack/thread` | 스레드 답글 |
| GET | `/api/slack/file` | 인증된 파일 프록시 |

모든 브라우저 API 호출은 `credentials: include`를 사용합니다.

## 시연 이후 보류한 다음 단계

1. 체크리스트·답변 초안 생성/발송
2. 범용 파일 업로드·S3·OCR과 Gmail 첨부 추출
3. Slack Events/Gmail push, 증분 historyId, 큐/워커
4. 페이지네이션·검색·실시간 갱신
5. 연동 해제·provider revoke, 운영 rate limit과 보관 정책

상세 흐름은 [DATA_AI_PIPELINE.md](./DATA_AI_PIPELINE.md), 확정 DTO와 API는
[PRODUCT_API_DESIGN.md](./PRODUCT_API_DESIGN.md)에 있습니다.

## 검증과 배포

로컬 임시 MongoDB E2E에서 회원가입, 사용자 분리, 계약 반영 멱등성,
Gmail·Slack OAuth 시작 URL과 8000 callback 생성을 검증했습니다. 실제 provider
callback은 client secret과 콘솔 등록 후 확인합니다.

배포는 AWS로 진행하며 CI/CD 안이 오면 서비스 구성을 고정합니다. 프론트와 API가
서로 다른 사이트라면 HTTPS와 cookie 설정을 함께 맞춰야 합니다.
