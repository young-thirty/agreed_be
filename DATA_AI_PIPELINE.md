# Agreed 채널 데이터·AI 처리 설계

> 대시보드 시안과 최종 기획 명세를 백엔드 데이터 흐름으로 옮긴 문서다.
> 공개 DTO와 API의 단일 기준은 `PRODUCT_API_DESIGN.md`이며 아래 안전 경계를 유지한다.

## 1. 한 줄 구조

```text
Gmail / Slack
  → 원문 수집·중복 제거
  → 프로젝트에 귀속된 SourceMessage 저장
  → AI 단계별 분석
  → 코드가 근거를 원문·문서와 재검증
  → 사람이 체크리스트와 답변 초안을 확인
  → 사람이 합의한 변경만 새 ContractVersion으로 반영
```

프론트엔드는 provider token을 받거나 AI를 직접 호출하지 않는다. 로그인 세션으로
FastAPI를 호출해 저장된 원문·분석 결과만 받는다.

## 2. 로그인과 연동

앱 로그인과 외부 채널 연동은 서로 다른 두 단계다.

1. 사용자가 이름·이메일·비밀번호로 Agreed에 회원가입/로그인한다.
2. 로그인한 사용자가 Gmail 또는 Slack 연결을 별도로 승인한다.
3. FastAPI callback이 provider token을 받아 암호화해 MongoDB에 저장한다.
4. 이후 수집기는 `ownerId + provider + 외부 계정/워크스페이스`로 토큰을 찾는다.

브라우저에는 HttpOnly 앱 세션 쿠키만 남는다. Google refresh token과 Slack bot
token은 localStorage, JavaScript가 읽을 수 있는 쿠키, API 응답에 등장하지 않는다.

## 3. 저장 단위

| 문서 | 역할 | 변하지 않는 식별 기준 |
|---|---|---|
| `User` | Agreed 로그인 사용자 | 사용자 ID |
| `Project` | 한 계약과 관련 자료·대화를 묶는 단위 | `ownerId + projectId` |
| `IntegrationConnection` | 암호화한 Gmail/Slack 권한 | 사용자 + provider 계정/워크스페이스 |
| `ProjectMaterial` | 계약서·제안서·요구사항 문서·회의록과 추출 텍스트 | 프로젝트 + 자료 ID + 버전 |
| `SourceMessage` | 수정하지 않는 Gmail/Slack 원문 | provider + 계정 + provider message ID |
| `AnalysisRun` | 특정 원문·문서 버전에 대한 AI 산출물 | source ID + prompt/model/document 버전 |
| `Requirement` | 사람이 처리하는 요구사항 업무 흐름 | 프로젝트 + requirement ID |
| `ReplyDraft` | 체크리스트 선택을 반영한 답변 초안 | analysis ID + checklist 버전 |
| `ContractVersion` | 승인 후 쌓이는 계약 버전 | 프로젝트 + version |
| `AuditEvent` | 누가 무엇을 승인·반영했는지 | append-only event ID |

`SourceMessage`와 `ProjectMaterial` 원문은 AI 결과로 덮어쓰지 않는다. 재분석은 기존
결과를 수정하지 않고 새 `AnalysisRun`을 만든다. 그래야 모델이나 프롬프트를 바꾼 뒤
왜 결과가 달라졌는지 추적할 수 있다.

## 4. 수집과 프론트 경계

### 수집

- Gmail은 `message.id`, Slack은 `team_id + channel_id + ts`를 중복 제거 키로 쓴다.
- callback/webhook은 원문 저장 또는 작업 등록까지만 하고 빠르게 성공 응답한다.
- AI 호출은 수집 요청 안에서 오래 붙잡지 않고 별도 작업으로 실행한다.
- 초기 이관은 `feat/#6`의 조회 API를 FastAPI에서 실행한다. 배포 후에는 Slack Events,
  Gmail 증분 동기화(`historyId`) 또는 스케줄러로 교체해도 정규화 이후 단계는 같다.
- 같은 이벤트가 재전송돼도 unique key로 한 번만 저장하고 분석 작업도 idempotency key로
  한 번만 만든다.

### 프론트가 받는 값

프로젝트 화면은 원문 목록과 검증이 끝난 분석 결과만 받는다.

```text
GET /api/projects/{projectId}/requests
  requestId, projectId
  sourceChannel: GMAIL | SLACK
  senderDisplay: 메일 주소 또는 Slack 표시 이름 | null
  aiProcessingStatus: PENDING | PROCESSING | COMPLETED | FAILED
  summaryTitle: string | null
  aiDecisionStatus: IN_SCOPE_ACTION_REQUIRED
                    | OUT_OF_SCOPE_COORDINATION_REQUIRED
                    | EXTRA_REQUEST
                    | null
  responseStatus: WAITING | COMPLETED
```

목록 정렬은 원문 발생 시각 내림차순이며 시연 MVP에서는 전체 배열을 반환한다. 프론트가 Gmail
20통·Slack 50통을 매번 다시 provider에서 가져와 합치는 구조는 운영 구조로 쓰지 않는다.

## 5. AI는 한 번에 전부 시키지 않는다

각 단계는 Pydantic 구조화 출력과 고정된 입력·출력을 갖는다.

1. **정규화** — 서명, 이전 답장 인용, Slack 시스템 이벤트를 분리하고 발화 ID를 붙인다.
2. **요청 추출** — 원문에서 클라이언트의 요청 문장과 짧은 요약만 찾는다.
3. **계약 대조** — 현재 `ContractVersion`과 프로젝트 문서에서 관련 조항 후보를 찾는다.
4. **판정** — 아래 세 값 중 하나와 짧은 이유·근거 ID만 생성한다.
5. **근거 검증** — 코드가 인용문이 실제 원문/문서에 존재하는지 다시 확인한다.
6. **체크리스트** — 사람이 답변 전에 확인할 범위·납기·비용·질문 항목을 만든다.
7. **답변 초안** — 사용자가 선택·수정한 체크리스트만 입력으로 받아 생성한다.

요청 카드의 색 판정(`AiDecisionStatus`)과 합의 진행 상태(`RequirementStatus`)는 다른
값이다. 초록 카드가 곧 합의 상태라는 뜻이 아니다.

| 판정 | 색 | 기준 |
|---|---|---|
| `IN_SCOPE_ACTION_REQUIRED` | 초록 | 현 계약·문서에 요청을 직접 뒷받침하는 조항이 있고 충돌이 없음 |
| `OUT_OF_SCOPE_COORDINATION_REQUIRED` | 주황 | 표현이 애매함, 자료가 부족함, 경계에 걸리거나 작은 범위 확대 가능성이 있음 |
| `EXTRA_REQUEST` | 빨강 | 새 산출물·명시적 범위 추가·납기/비용 변경처럼 계약 밖 변경 근거가 분명함 |

근거가 부족하거나 서로 충돌하면 초록·빨강을 억지로 고르지 않고 주황으로 내린다.

## 6. 화면에 내보낼 근거

판정 하나에는 최소 두 종류의 근거를 연결한다.

- 요청 근거: `sourceMessageId`, 발화 ID, 실제 요청 인용문
- 계약 판단 근거: `documentId`, 문서 버전, 페이지/문단 위치, 실제 인용문

LLM이 만든 인용문을 그대로 믿지 않는다. 정규화한 인용이 저장 원문에 포함되는지
코드가 확인하고, 문서 버전과 내용 hash도 맞는 경우에만 화면에 표시한다. 하나라도
검증되지 않으면 해당 근거를 버린다. 판정을 지지할 근거가 남지 않으면
`OUT_OF_SCOPE_COORDINATION_REQUIRED`로 강등하고 "확인 가능한 근거가 부족합니다"를 표시한다.

내부 chain-of-thought는 저장·표시하지 않는다. 사용자가 검증할 수 있는 짧은 판단 이유와
실제 인용만 저장한다.

## 7. 계약 변경 안전장치

AI는 계약 문서를 직접 수정하지 않는다.

```text
AnalysisRun
  → 변경분 초안(범위 추가/제거, 납기 before/after, 금액 delta)
  → 사람 체크·합의
  → POST contract/apply (idempotency key + expected version)
  → ContractVersion N+1 + ContractDiff + AuditEvent
```

- 금액·납기·수락 여부는 사람이 입력·확정한다.
- 동일 requirement를 다시 apply해도 한 번만 반영한다.
- 반영 도중 버전이 바뀌면 조용히 덮어쓰지 않고 최신 버전에서 다시 확인한다.
- 답변 전송과 계약 반영은 별도 버튼·권한이다. 답변 초안 생성만으로 어느 것도 실행하지 않는다.

## 8. 프롬프트 인젝션·개인정보 방어

- 메일, Slack 메시지, 업로드 문서는 모두 신뢰하지 않는 데이터로 취급한다. 그 안의
  "이전 지시를 무시하라" 같은 문장을 시스템 명령으로 실행하지 않는다.
- 분석 모델에는 provider 전송·DB 수정·계약 apply 도구를 주지 않는다.
- 모델 출력은 허용된 enum과 길이 제한이 있는 Pydantic 스키마로 다시 검증한다.
- prompt/model/schema/document 버전, 입력 source ID, 처리 시간, 실패 유형을 기록한다.
  provider token과 불필요한 전체 원문은 로그에 남기지 않는다.
- 연동 해제 시 provider token을 revoke하고 ciphertext를 삭제한다. 원문 보관·삭제 기간은
  기능 확정서와 개인정보 처리방침에서 명시한다.

## 9. 구현 순서

1. 앱 로그인·사용자별 소유권과 provider token 암호화
2. `feat/#6` Gmail/Slack OAuth·조회 어댑터의 FastAPI 이관
3. `Project`, `ProjectMaterial`, `SourceMessage`와 증분 수집
4. 단계별 `AnalysisRun`과 근거 검증
5. 체크리스트·답변 초안
6. 멱등 계약 반영·감사 로그
7. 배포 후 Slack Events / Gmail 증분 수집 worker와 평가 데이터셋

공개 필드·API는 `PRODUCT_API_DESIGN.md`와 Swagger를 함께 갱신한다.
