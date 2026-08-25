# Agreed 제품·API 설계 (최종 기획 기준)

대상은 UNI-17/18/20/21과 요청·자료 목록이다. FastAPI + MongoDB + Beanie에
바로 적용하되 내일 10분 시연을 막는 대규모 인프라는 보류한다.

## 1. 확정 경계와 현재 차이

- 앱 로그인과 Gmail/Slack 연결은 별개다. 로그인만 성공해도 대시보드로 간다.
- 채널은 `GMAIL`, `SLACK`만 허용한다. 프론트는 provider token/원문을 저장하지 않는다.
- 현재 세션 로그인, OAuth, Gmail/Slack 조회는 구현되어 있다.
- `phoneNumber`와 공개 사용자 DTO의 기존 차이는 이번 명세 반영에서 수정했다.
- `Project`, `ProjectSourceLink`, `SourceMessage`, `ClientRequest`, `ProjectMaterial`,
  `AnalysisRun`과 프로젝트 화면 API를 구현했다.
- `Contract`/`Requirement`에 optional `projectId`와 프로젝트 전용 경로를 추가했다.
- 기존 `/api/analyze`의 요구사항 상태 추출과 새 요청 요약·3색 판정은 별도 파이프라인이다.

## 2. 화면 ↔ API

공통 응답은 `{ "ok": true, "data": ... }` 또는 `{ "ok": false, "error": "..." }`다.

| 화면/동작 | API | 성공 data |
|---|---|---|
| UNI-17 회원가입 | `POST /api/auth/signup` | `{ user: UserSummary }` |
| UNI-18 로그인 | `POST /api/auth/login` | `{ user: UserSummary }` |
| 사용자/로그아웃 | `GET /api/auth/me`, `POST /api/auth/logout` | user / loggedOut |
| UNI-20 목록 | `GET /api/projects?status=&sort=` | `ProjectSummary[]` |
| UNI-21 상단 | `GET /api/projects/{projectId}` | `ProjectSummary` |
| 요청 탭 | `GET /api/projects/{projectId}/requests` | `ClientRequestSummary[]` |
| 요청 단건 | `GET /api/requests/{requestId}` | 동일 DTO + 원문·근거 |
| 답변 전 확인 항목 | `POST /api/requests/{requestId}/checklist` | `{ items: string[] }` |
| 답변 초안 생성 | `POST /api/requests/{requestId}/reply-draft` | `{ body: string }` |
| 대응 상태 변경 | `PATCH /api/requests/{requestId}/response-status` | 동일 DTO |
| 자료 탭 | `GET /api/projects/{projectId}/materials?ticketId=` | `ProjectMaterialSummary[]` |
| 자료 티켓 할당·해제 | `PATCH /api/projects/{projectId}/materials/{materialId}` | `ProjectMaterialSummary` |

정렬은 `status`(기본: ACTIVE→DRAFT→COMPLETED, 동률 updatedAt 내림차순),
`updatedAt` 내림차순, `createdAt` 내림차순만 허용한다. 잘못된 enum/sort는 422,
없거나 타인 소유인 자원은 같은 404다. 시연 MVP는 빈 배열을 포함한 전체 배열 반환이며
cursor pagination은 보류한다.

## 3. exact enum과 모순 결정

```text
ProjectStatus      ACTIVE | DRAFT | COMPLETED
ProjectSort        status | updatedAt | createdAt
SourceChannel      GMAIL | SLACK
ProcessingStatus   PENDING | PROCESSING | COMPLETED | FAILED
AiDecisionStatus   IN_SCOPE_ACTION_REQUIRED
                   | OUT_OF_SCOPE_COORDINATION_REQUIRED
                   | EXTRA_REQUEST
ResponseStatus     WAITING | COMPLETED
Direction          RECEIVED | SENT
DocumentType       PROPOSAL | CONTRACT | REQUIREMENTS | MEETING_NOTES | OTHER
MaterialOrigin     CHANNEL | MANUAL
AnalysisTargetType CLIENT_REQUEST | MATERIAL_CLASSIFICATION
```

색 매핑은 초록=`IN_SCOPE_ACTION_REQUIRED`, 주황=`OUT_OF_SCOPE_COORDINATION_REQUIRED`,
빨강=`EXTRA_REQUEST`다. AI 미완료/실패 시 summary와 decision은 null이다. AI 판정과
사람의 `responseStatus`는 독립이다.

`Reject`는 정렬 설명 한 곳에만 있고 상태 정의·Logic·Output·완료 조건에는 없다.
따라서 MVP 공개 enum에서는 `REJECTED`를 제외한다. FE 화면이 확정될 때 마지막 순위로
추가한다.

## 4. exact 공개 DTO

Mongo `_id`, `ownerId`, token은 노출하지 않는다. date는 `YYYY-MM-DD`, datetime은
UTC ISO 8601이다.

```text
UserSummary
  userId:string, name:string, email:string, phoneNumber:string|null, createdAt:datetime

ProjectSummary
  projectId:string, name:string, clientName:string
  startDate:date|null, endDate:date|null, contractPrice:int|null
  unansweredRequestCount:int, createdAt:datetime, updatedAt:datetime
  status:ProjectStatus

ClientRequestSummary
  requestId:string, projectId:string, sourceChannel:SourceChannel
  senderDisplay:string|null, aiProcessingStatus:ProcessingStatus
  summaryTitle:string|null, aiDecisionStatus:AiDecisionStatus|null
  responseStatus:ResponseStatus

ProjectMaterialSummary
  materialId:string, projectId:string, ticketId:string|null, fileName:string, direction:Direction
  communicatedAt:datetime, classificationStatus:ProcessingStatus
  documentType:DocumentType|null, summary:string|null
```

Signup은 name/email/password/phoneNumber가 모두 필수다. 기존 시연 계정만
phoneNumber가 null일 수 있다. 로그인은 email/password만 받고
HttpOnly `agreed_session` cookie를 발급한다. UNI-20/21은 같은 Project 변환 함수,
UNI-21/22 요청은 같은 ClientRequest DTO를 쓴다. 원문·근거·체크리스트·답장 상세는
명세 필드가 아직 확정되지 않아 공개 DTO에서 보류한다.

## 5. Beanie 모델·인덱스·소유권

신규 비즈니스 문서는 `ownerId`, `projectId: PydanticObjectId`와 UTC timestamp를 갖고,
ownerId는 body에서 받지 않는다. 기존 `IntegrationConnection.ownerId`는 string이므로
MVP OAuth 소유권 비교는 `str(current_user.id)`를 유지한다.

### User / Project

- `User.phoneNumber: str|null`: 기존 문서 호환상 DB default null, 신규 Signup에서는 필수.
- `Project`: ownerId, name, clientName, startDate?, endDate?, contractPrice?, status,
  statusRank(ACTIVE=0/DRAFT=1/COMPLETED=2), createdAt, updatedAt.
- Project 인덱스: `(ownerId,statusRank,updatedAt DESC)`, `(ownerId,updatedAt DESC)`,
  `(ownerId,createdAt DESC)`.

### ProjectSourceLink / SourceMessage

- `ProjectSourceLink`: ownerId, projectId, connectionId, sourceChannel, displayName,
  Gmail counterpartyEmail/threadId 또는 Slack teamId/channelId, locatorKey, timestamps.
- link 인덱스: `(ownerId,projectId)` 및
  `(ownerId,projectId,sourceChannel,connectionId,locatorKey)` unique.
- `SourceMessage`: ownerId, projectId, sourceLinkId, connectionId, sourceChannel,
  sourceKey, providerMessageId/threadId, senderExternalId/display, conversationDisplay,
  direction, rawText, occurredAt, contentHash, attachmentRefs, createdAt.
- Gmail sourceKey=`연결계정:message.id`, Slack=`teamId:channelId:ts`.
- message 인덱스: `(ownerId,sourceChannel,connectionId,sourceKey)` unique,
  `(ownerId,projectId,occurredAt DESC)`.

### ClientRequest / ProjectMaterial / AnalysisRun

- `ClientRequest`: ownerId, projectId, sourceMessageId, analysisRunId?, requestOrdinal,
  sourceChannel, senderDisplay?, occurredAt, aiProcessingStatus, summaryTitle?,
  aiDecisionStatus?, responseStatus=WAITING, requestEvidence?, documentEvidence[], timestamps.
- request 인덱스: `(ownerId,projectId,occurredAt DESC)`,
  `(ownerId,projectId,responseStatus)`, `(ownerId,sourceMessageId,requestOrdinal)` unique.
- `ProjectMaterial`: ownerId, projectId, origin, sourceMessageId?, connectionId?,
  providerFileId?, fileName, mimeType?, sizeBytes?, storageKey?, extractedText?, direction,
  communicatedAt, classificationStatus, documentType?, contentHash?, timestamps.
- material 인덱스: `(ownerId,projectId,communicatedAt DESC)`, `(ownerId,sourceMessageId)`,
  `(ownerId,connectionId,providerFileId)` partial unique.
- `AnalysisRun`: ownerId, projectId, targetType, sourceMessageId?/materialId?, status,
  inputHash, promptVersion, model, errorCode?, startedAt?, completedAt?, timestamps.
- run 인덱스: `(ownerId,projectId,status,createdAt)`,
  `(ownerId,targetType,inputHash,promptVersion)` unique. 충돌 시 기존 run을 재사용한다.

모든 신규 Document를 `models/__init__.py::DOCUMENT_MODELS`에 등록한다. 접근은 먼저
`get_owned_project(projectId,userId)`로 확인한 뒤 하위 자원도 항상
`ownerId + projectId + _id`로 조회한다. ProjectSourceLink가 가리키는 connection도
현재 사용자 소유인지 확인한다.

`unansweredRequestCount`는 WAITING 요청 수다. Project에 중복 저장하지 않는다. 목록은
조회된 project ID들을 한 번의 aggregation으로 group하고, 상세는 복합 index로 count한다.

## 6. Contract·Requirement projectId 전환

두 Document에 `projectId`를 추가한다. Contract unique index는
`(ownerId,projectId,version)`과 `(ownerId,projectId,appliedRequirementId)` partial,
Requirement index는 `(ownerId,projectId)`와 `(ownerId,projectId,status)`로 바꾼다.
`_current_contract` 및 transition/apply의 모든 쿼리도 ownerId+projectId를 사용한다.

정식 경로는 `/api/projects/{projectId}/contract[/apply]`와
`/api/projects/{projectId}/requirements`다. 기존 owner-only API는 FE 전환 동안만
호환하고, 여러 프로젝트 중 하나를 임의 선택하지 않는다. 기존 문서는 사용자별 기본
Project를 만든 후 projectId를 한 번 backfill하며 DB 전체 삭제는 필요 없다.

## 7. 수집·AI 처리

연동 확인용 기존 provider API와 프로젝트 저장 데이터는 구분한다.

```text
GET/POST /api/projects/{projectId}/source-links
POST     /api/projects/{projectId}/source-links/{sourceLinkId}/sync
GET      /api/analysis-runs/{analysisRunId}
```

FE는 Gmail 상대 또는 Slack channel만 선택한다. FastAPI가 OAuth token으로 조회하고
SourceMessage를 unique upsert한 뒤 새 원문에 AnalysisRun을 만든다. 프로젝트 화면은
Mongo에 저장된 결과만 읽는다. FE가 provider 원문 배열을 재전송하는 API는 만들지 않는다.

시연에는 Redis/Celery/Kafka를 넣지 않는다. provider 조회·upsert 후 AI만 FastAPI
`BackgroundTasks`로 실행하고 sync는 신규 메시지 수와 run ID를 즉시 반환한다.

ClientRequest 파이프라인(`infra/llm/orchestrator.py`):

1. run을 PROCESSING으로 바꾸고 서명/이전 인용/Slack 시스템 문장을 정규화한다.
2. 요청을 0~5개 추출한다. 원문 한 건에 요청이 여럿이면 각각 `requestOrdinal`을
   달리해 별도 ClientRequest로 저장한다(이전에는 원문 1건=요청 1건 고정이었다).
3. 요청마다 계약 대조 서브 에이전트(`infra/llm/subagents/contract_match.py`)에
   위임한다. `read_contract`/`search_materials` 도구로 현재 계약과 프로젝트 자료를
   직접 찾아보고 판정한다. 여러 요청의 대조는 병렬로 돌린다.
4. Pydantic enum/길이와 실제 substring(`core/grounding.py:is_quote_in`)을 검증한
   근거만 저장한다. 도구가 보여준 적 없는 문서를 근거로 대면 버린다.
5. ClientRequest를 requestOrdinal별로 upsert한다. 재분석으로 요청 수가 줄면 남는
   ordinal은 삭제한다. 성공은 COMPLETED, 예외는 FAILED다.
6. `EXTRA_REQUEST`(빨강) 판정은 `app/requirement_sync.py`가 프로젝트의
   `Requirement`(9상태, 초기 상태 '요청')로 하나씩 연결한다. `sourceRequestId`가
   멱등 키다. 이 연결은 코드가 하고 AI는 관여하지 않는다.

직접 계약 근거가 있으면 초록, 애매/근거 부족이면 주황, 명시적 새 산출물·범위 추가가
문서로 확인될 때만 빨강이다. 허구 인용을 버린 뒤 근거가 없으면 주황으로 낮춘다.
AI는 합의, response 완료, contract apply를 하지 않는다.

체크리스트·답변 초안은 sync 때 함께 만들지 않고 사람이 요청 카드를 열 때
on-demand로 만든다.

```text
POST /api/requests/{requestId}/checklist     사람이 확인할 항목 3~5개
POST /api/requests/{requestId}/reply-draft   선택한 항목만 반영한 답변 초안(발송 안 함)
PATCH /api/requests/{requestId}/response-status   WAITING ↔ COMPLETED. AI는 관여하지 않는다
```

자료는 파일명+추출 텍스트로 5종 중 하나를 구조화 분류한다. 정상 분류의 그 외 문서는
OTHER, 파일/모델 작업 실패만 FAILED/null이다. DeepSeek은 기존 8초 timeout, SDK retry 0,
스키마 실패 1회 재시도를 유지하고 고정 시연 입력은 contentHash 결과를 미리 저장한다.

## 8. Swagger와 MVP 순서

기본 `/docs`, `/openapi.json`, `/redoc`을 사용한다. 현재 `JSONResponse`만으로는 DTO가
안 보이므로 `ApiSuccess[T]`, `ApiError` Pydantic schema와 endpoint `response_model`,
401/404/422 example을 선언한다. 태그는 auth/projects/client-requests/materials/
integrations/analysis/contracts로 나눈다. 세션 dependency는
`APIKeyCookie(name="agreed_session", auto_error=False)`로 표시한다.

완료: phoneNumber+정확한 사용자 DTO, Swagger 전역 응답·인증·OAuth 문서화.
완료: 프로젝트·요청·자료 모델/조회 API, source-link 등록·sync, 분석 실행 조회,
프로젝트별 계약·요구사항 경로, 시연 seed 스크립트.
완료: 요청 다건 추출(1건→N건), 계약 대조 서브 에이전트(substring 매칭 대체),
`EXTRA_REQUEST`→`Requirement` 합의 흐름 연결, 체크리스트·답변 초안 생성 API,
대응 상태(`responseStatus`) 변경 API. 상세는 DATA_AI_PIPELINE.md 5-a절.

P0/P1 구현: Project/Request/Material 모델 → 목록·상세 API → unanswered/소유권 →
Contract/Requirement projectId → 시연 seed → Gmail 상대 1개·Slack channel 1개 sync
→ DeepSeek 요청 분석·자료 분류. (완료)

Git 탐색 서브 에이전트(`POST /api/projects/{id}/git/ask`)와 Slack 파일의 S3 원본
저장(`ProjectMaterial.storageKey`)을 추가했다. `SourceChannel`에 `GITHUB`이 늘었다.
레포 연결은 OAuth가 아니라 서버 `GITHUB_TOKEN`(PAT)으로 clone하며, source-link의
`repoFullName`("owner/repo")만 있으면 된다. 워크스페이스는 질문마다 얕게 clone하고
답변 후 지운다 — 캐시하지 않는다. S3는 버킷 미설정 시 조용히 건너뛴다.

보류: Slack Events/Gmail history/watch, worker queue, pagination/검색/실시간 전송,
OCR·텍스트 추출(원본은 S3에 저장하지만 ProjectMaterial.extractedText 채우는 것은 별도),
Gmail 첨부(attachmentRefs 비어 있음), 수동 업로드 처리, git 탐색 결과를 요청 판정에
자동으로 엮는 것(현재는 독립 호출), 요청 근거 상세 DTO의 공식 확정(현재
`GET /api/requests/{id}`가 비공식으로 내려줌), 답장 실제 발송(초안 생성까지만),
감사 로그, REJECTED 상태. 기존 ContractDiff와 합의 후 apply는 project 범위로 유지한다.
