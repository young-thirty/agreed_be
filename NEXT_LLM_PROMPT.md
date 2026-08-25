# 다음 LLM 작업 프롬프트 — 화면 1~9 기능 연결 마무리

아래 내용을 새 작업의 첫 프롬프트로 그대로 사용한다.

---

너는 Agreed 백엔드 저장소 `young-thirty/agreed_be`를 이어서 작업한다.
현재 기준 브랜치는 `main`, 기준 커밋은 이 문서를 포함하는 최신 `origin/main`이다.
서비스는 짧은 시연용이므로 보안 재검토나 대규모 리팩터링에 시간을 쓰지 말고,
이미 확정된 화면 1~9가 실제 MongoDB 데이터와 AI 결과로 동작하도록 기능 연결을
끝내는 데 집중한다.

## 반드시 지킬 작업 방식

- 시작하자마자 `git fetch origin main` 후 로컬 `main`을 fast-forward 한다.
- `deploy/ter`는 사용자의 미추적 파일이므로 수정·삭제·커밋하지 않는다.
- `agreed_dev`를 수정해야 한다면 먼저 사용자에게 수정 범위와 이유를 한 줄로 알린다.
- 중간 보고는 원인/현재 조치/남은 작업을 짧게 공유한다. 긴 보안 감사 금지.
- 기능 검증 후 사용자의 기존 지시에 따라 `main`에 커밋하고 push한다.

## 제품 흐름과 용어

1. Gmail/Slack에서 고객 → 프리랜서 메시지를 수집해 `SourceMessage`로 저장한다.
2. 한 메시지에서 요청 N개를 추출해 `ClientRequest` 티켓 후보를 만든다.
3. 프로젝트·기존 티켓·계약/제안서/회의록과 대조한다.
4. 티켓별 AI 솔루션을 만든다.
5. 사람이 티켓 생성/병합/무시, 상태, 추가 금액·납기, 답장 여부를 결정한다.
6. AI는 비개발 언어로 답장 초안을 만들고, 실제 결정과 발송은 사람이 한다.

Inbound는 고객 → 프리랜서, Outbound는 프리랜서 → 고객이다. 티켓 생성은
Inbound만 유발한다. Outbound는 기존 프로젝트/티켓 맥락 갱신에만 사용한다.

## AI 솔루션 1~6단계

현재 `POST /api/requests/{requestId}/solution`은 다음 재료를 조합해 결과를 MongoDB의
`ClientRequest.solution`에 캐시한다. `refresh=true`일 때만 재생성한다.

1. 요구 해석: 저장된 티켓 추출 결과 사용
2. 합의 확인: 계약 및 프로젝트 자료에서 근거 검색
3. 개발 현황 확인: 연결된 GitHub 저장소 읽기
4. 영향 분석: 변경 영역·부작용·테스트 범위 분석
5. 작업 가능 여부: 근거 부족 시 사람 판단이 필요하다고 표시해 환각 억제
6. 답변 생성: 위 결과를 고객용 비개발 언어로 종합

주요 결과는 `adviceMessage`, `adviceReason`, `basisQuote`, `basisDocumentId`,
`scopeDecision`, `developmentStatus`, `impactAnalysis`, `feasibility`, `replyDraft`,
`relatedFiles`, `generatedAt`이다. DeepSeek 키가 없거나 호출이 실패하면 시연용
폴백이 반환된다. GitHub 미연결 프로젝트는 개발 현황을 확인할 수 없다고 명확히
표시해야 한다.

## 이미 구현되어 검증된 백엔드

- 자체 로그인 세션, Gmail OAuth/조회, Slack OAuth/조회 및 프로젝트 source sync
- 프로젝트·원문·티켓·자료·분석 결과 MongoDB 저장 및 사용자 귀속
- `GET /api/projects`와 프로젝트 CRUD
- `GET /api/tickets`, `GET /api/tickets/{ticketId}`
- `POST /api/requests/{requestId}/solution`
- `PATCH /api/requests/{requestId}/ticket-status`
- 티켓 판단/답장 초안/mark-sent API
- `GET /api/projects/{projectId}/messages`
- `GET /api/projects/{projectId}/context`
- 티켓 상세의 `analysis.intents`, 고정 4개 판단 필드, `devContext`, GitHub/문서 근거
- 로컬 MongoDB E2E에서 solution 생성·캐시, 티켓 상세, 프로젝트 메시지/컨텍스트 응답 확인

Gmail/Slack OAuth 자체는 이미 동작한다. 운영 callback은 다음과 같다.

```text
https://nncjwb3g74.ap-northeast-1.awsapprunner.com/api/email/callback
https://nncjwb3g74.ap-northeast-1.awsapprunner.com/api/slack/callback
```

OAuth를 다시 설계하지 않는다. 실시간 Gmail push/Slack Events가 아니라 현재는
사용자가 연결한 source를 sync하는 구조다. 실제 Gmail/Slack 발송은 아직 없고
`mark-sent`는 서비스 내부 상태 기록이다.

## 화면 6~9 기준 현재 정합성과 남은 작업

### 화면 6 — 프로젝트 목록

프로젝트명, 상태, 고객 이메일, GitHub, 최근 고객 메시지/시간 필드는 이미 응답한다.
그러나 `app/api/projects.py::_project_card`가 active 티켓 수 하나를
`activeTicketCount`와 `unansweredMessageCount` 양쪽에 똑같이 넣는 버그가 있다.

가장 먼저 다음을 구현한다.

- `activeTicketCount`: `ticketStatus == "active"`인 티켓 수
- `unansweredMessageCount`: 고객이 보낸 메시지 중 아직 답장 완료되지 않은 메시지 수
- `TicketDecision.sentAt` 또는 실제 응답 상태를 기준으로 계산하고 테스트 추가

### 화면 7 — 프로젝트의 티켓 탭

티켓 코드·카테고리·제목·요구사항·최근 요청·현재 상태·상태 변경 API는 있다.
조회 DTO는 `Active|Done|Reject`, 변경 API 입력은 `active|done|rejected`라서 현재
표현이 다르다. 백엔드가 화면용 값도 받아 정규화하거나 프론트 어댑터 하나로
통일한다. 데모 병목을 줄이기 위해 백엔드에서 두 표현을 모두 허용하는 편이 낫다.

### 화면 8 — 프로젝트 컨텍스트(GitHub 연결)

문서와 GitHub 개발 현황 조회는 있지만 현재 `developmentStatus`는
`targetFeature/currentState/relatedPaths/relatedRefs` 중심이다. 화면은 다음 구조를
기대한다.

```json
{
  "repo": "acme/website",
  "features": [
    {"name": "로그인", "items": [{"state": "done|progress|todo", "text": "..."}]}
  ],
  "openWork": [{"title": "PR #42 ...", "note": "..."}]
}
```

기존 GitHub 근거를 버리지 말고 위 화면 DTO로 투영한다. 자료 DTO도 프론트가 쓰는
`id/kind`와 백엔드의 `materialId/documentType` 차이를 한쪽 어댑터에서 정리한다.

### 화면 9 — 프로젝트 컨텍스트(GitHub 미연결)

현재처럼 `development: null`과 문서 빈 배열을 내려 화면이 “GitHub 미연결”을
표시하게 하면 된다. 이 흐름은 이미 지원된다.

## 화면 1~5와의 연결에서 꼭 확인할 것

- 프로젝트 sync → 원문 저장 → 요청 다건 추출 → 티켓 매칭/생성 → solution 조회가
  하나의 저장 데이터 흐름으로 이어져야 한다.
- 티켓이 만들어진 직후 solution은 자동 생성되지 않는다. 지금은 화면 진입 시
  `POST /api/requests/{id}/solution`을 한 번 호출하고 이후 캐시를 쓰는 방식이다.
  시연에는 이 방식이 충분하므로 프론트에서 로딩 상태와 함께 연결한다.
- `GET /api/tickets/{id}`의 `analysis.fields`는 반드시 `범위/개발/일정/사용자 판단 필요`
  네 칸을 유지한다.
- 근거 문장과 근거 문서는 실제 `requestEvidence`, `documentEvidence`, GitHub 경로를
  사용한다. 근거가 없으면 만들어내지 말고 “확인하지 못함”으로 반환한다.
- 사람이 입력한 추가 비용·납기와 판단은 기존 decision/contract apply 흐름을 사용한다.
- 현재 `ticket` 근거는 AI가 고른 매칭 결과가 아니라 같은 프로젝트의 최근 티켓
  최대 3개다. 매칭 결과 ID를 저장·재사용해 무관한 티켓이 근거로 노출되지 않게 한다.
- `POST /solution`은 계약 대조와 GitHub clone을 함께 실행한다. 화면의 개발 확인
  버튼에서 최초 호출할지, 개발 현황 API를 분리할지 정해 버튼의 의미와 맞춘다.
- 기본 `replyDraft`는 solution에 먼저 생기지만 화면에서는 사람의 처리 방식 결정
  전까지 숨긴다. 결정값을 반영한 최종 초안은 `/reply-draft`로 다시 만든다.
- `feasibility`는 구조화된 AI 판단이며 사실을 독립 검증하는 판정기가 아니다.
  저장소 미연결·근거 부족이면 `needs_clarification`으로 내려가도록 유지한다.

## 프론트 연동 상태

최신 참고 프로토타입은 `agreed_dev`의 `origin/hyun907/ui-다듬기` 커밋
`6f572e8`이다. 화면은 최신이지만 프로젝트/티켓/문서/개발현황 일부가 아직 mock과
localStorage 기반이다. 백엔드는 데이터 원천이 MongoDB이므로 서버 데이터를
localStorage에 복제하지 않는다.

프론트 연결 시 공통 조건:

```text
NEXT_PUBLIC_API_BASE_URL=https://nncjwb3g74.ap-northeast-1.awsapprunner.com
fetch(..., { credentials: "include" })
```

프론트 작업 전 사용자에게 알리고, 다음 순서로 mock을 제거한다.

1. 프로젝트 목록 → `GET /api/projects`
2. 프로젝트 티켓 → `GET /api/tickets?projectId=...`
3. 고객 메시지 → `GET /api/projects/{id}/messages`
4. 컨텍스트 → `GET /api/projects/{id}/context`
5. 티켓 상세 진입 → solution 생성/캐시 조회 후 `GET /api/tickets/{id}` 재조회

## 바로 수행할 우선순위

1. 화면 6의 두 count를 실제 의미대로 분리하고 회귀 테스트
2. 티켓 상태 입력/출력 표현 정규화
3. 화면 8용 context DTO와 문서 필드 정합화
4. Swagger 예제와 `PRODUCT_API_DESIGN.md`, `USER_FLOW.md`를 실제 DTO와 동기화
5. 사용자에게 알린 뒤 프론트 mock을 위 API로 교체
6. 로컬 E2E와 배포 URL `/health`, `/docs` 확인 후 main push

완료 보고에는 “구현”, “검증”, “프론트가 해야 할 일”, “실제 발송/실시간 sync처럼
의도적으로 남긴 범위”를 분리해서 적는다. 구현하지 않은 것을 완료라고 말하지 않는다.

---
