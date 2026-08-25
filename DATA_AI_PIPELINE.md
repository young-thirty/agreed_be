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

`ReplyDraft`와 `AuditEvent`는 제품 확정 후 추가할 보류 모델이다. 현재 시연 구현은
Project부터 AnalysisRun까지의 채널 수집·요청 판정·자료 분류와 기존 Contract apply에 집중한다.

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
2. **요청 추출** — 원문에서 클라이언트의 요청을 0건 이상 찾는다. 한 건에 요청이 여러
   개면 각각을 따로 뽑는다(`infra/llm/orchestrator.py`).
3. **계약 대조** — 요청마다 현재 `Contract`와 프로젝트 `ProjectMaterial`에서 관련 조항을
   찾는다. 계약 문구를 그대로 대는 substring 매칭이 아니라, 도구를 쥔 서브 에이전트가
   필요하면 자료를 더 찾아보며 판단한다(`infra/llm/subagents/contract_match.py`, 5-a절).
4. **판정** — 아래 세 값 중 하나와 짧은 이유·근거 ID만 생성한다.
5. **근거 검증** — 코드가 인용문이 실제 원문/문서에 존재하는지 다시 확인한다.
6. **체크리스트** — 사람이 답변 전에 확인할 범위·납기·비용·질문 항목을 만든다
   (`POST /api/requests/{id}/checklist`).
7. **답변 초안** — 사용자가 선택·수정한 체크리스트만 입력으로 받아 생성한다
   (`POST /api/requests/{id}/reply-draft`). 생성만 하고 발송하지 않는다.

요청 카드의 색 판정(`AiDecisionStatus`)과 합의 진행 상태(`RequirementStatus`)는 다른
값이다. 초록 카드가 곧 합의 상태라는 뜻이 아니다.

| 판정 | 색 | 기준 |
|---|---|---|
| `IN_SCOPE_ACTION_REQUIRED` | 초록 | 현 계약·문서에 요청을 직접 뒷받침하는 조항이 있고 충돌이 없음 |
| `OUT_OF_SCOPE_COORDINATION_REQUIRED` | 주황 | 표현이 애매함, 자료가 부족함, 경계에 걸리거나 작은 범위 확대 가능성이 있음 |
| `EXTRA_REQUEST` | 빨강 | 새 산출물·명시적 범위 추가·납기/비용 변경처럼 계약 밖 변경 근거가 분명함 |

근거가 부족하거나 서로 충돌하면 초록·빨강을 억지로 고르지 않고 주황으로 내린다.

`EXTRA_REQUEST`(빨강) 판정을 받은 요청은 `app/requirement_sync.py`가 프로젝트의
`Requirement`(9상태)로 하나씩 만든다. 이 연결은 AI가 하지 않는다. "계약 밖 변경
근거가 분명한 요청은 사람이 판단할 요구사항이 된다"는 규칙이지 추론이 아니기
때문이다(6.1절). `Requirement.sourceRequestId`가 멱등 키라 재분석해도 중복 생성되지
않고, 사람이 이미 진행시킨 카드를 되돌리지도 않는다.

## 5-a. 에이전트 하네스 — 오케스트레이터와 서브 에이전트

3단계(계약 대조)처럼 "필요하면 자료를 더 찾아본다"는 판단은 JSON mode 단발 호출로는
못 만든다. 도구 호출 자체가 안 되기 때문이다. 그래서 이 단계만 function calling
기반의 서브 에이전트로 만들고, 나머지 단발 추출(2·6·7단계)은 기존 JSON mode 규약을
그대로 유지한다. 유어슈(Yourssu)의 사내 봇 [shookie](https://github.com/yourssu/shookie)의
"메인 에이전트는 조정만 하고 실제 판단은 서브 에이전트에 위임한다" 패턴을 참고했다.

```text
infra/llm/
  harness.py          run_json (단발 JSON mode) / run_agent (도구 호출 루프)
  orchestrator.py      원문 1건 → 요청 N건 추출 → 요청마다 계약 대조 위임(병렬)
  subagents/
    contract_match.py  계약 대조. read_contract / search_materials 도구 2개
    checklist.py        체크리스트. 도구 없음
    reply_draft.py       답변 초안. 도구 없음
```

다만 조정(orchestration) 자체를 LLM에 맡기지 않는다. "원문에서 요청을 뽑고, 각
요청을 계약과 대조한다"는 순서는 규칙이므로 `infra/llm/orchestrator.py`의 코드가
정하고, 판단(무엇이 요청인지, 계약 범위 안인지)만 모델에 맡긴다. 슬랙 봇처럼
사용자 발화를 실시간으로 라우팅할 필요가 없는, 내부 배치 파이프라인이기 때문이다.

`run_agent`가 지키는 규율:

- **컨텍스트 최소 전달** — 서브 에이전트에게는 요청 요약·인용·원문만 준다. 다른
  요청이나 무관한 프로젝트 데이터는 넘기지 않는다.
- **도구 접근은 읽기 전용** — `read_contract`, `search_materials` 모두 조회만 한다.
  계약을 바꾸거나 메일을 보내는 도구는 어떤 서브 에이전트에도 주지 않는다(8절).
- **턴 예산과 시간 예산을 분리** — `MAX_AGENT_TURNS`(도구 호출 최대 6회)와
  `AGENT_BUDGET_SECONDS`(전체 30초)를 함께 둔다. 턴 하나가 8초(client.py) 걸려도
  전체가 48초까지 늘어나지 않는다. 예산을 넘기면 도구를 빼고 결론만 한 번 더 물어
  검증 가능한 답을 받는다.
- **독립 조회는 병렬** — 한 원문에 요청이 여럿이면 계약 대조를 동시에 돌린다
  (`asyncio.gather`). 요청 3건을 직렬로 돌리면 시연에서 기다릴 수 없다.
- **소유권은 도구를 만들 때 묶는다** — `ownerId`·`projectId`는 도구 클로저에 미리
  박아두고, 모델이 인자로 지정하게 두지 않는다. 3절의 소유권 규칙이 도구 경계에도
  그대로 적용된다.

모델이 도구 결과에서 옮겨 적은 인용(`documentQuote`)은 하네스가 아니라
`core/grounding.py:is_quote_in`으로 다시 검증한다. 도구가 보여준 적 없는 문서를
근거로 대거나 인용이 허구면, 판정이 초록이었을 때만 주황으로 내린다(빨강은 "계약에
없다"는 사실 자체가 근거라 그대로 둔다). L2 근거 검증 규칙을 문서 대조에도 같은
방식으로 적용한 것이다.

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

1. 앱 로그인·사용자별 소유권과 provider token 암호화 (완료)
2. `feat/#6` Gmail/Slack OAuth·조회 어댑터의 FastAPI 이관 (완료)
3. `Project`, `ProjectMaterial`, `SourceMessage`와 sync (완료)
4. `AnalysisRun`·요청 판정·자료 분류와 근거 검증 (완료)
5. 요청 다건 추출 + 계약 대조 서브 에이전트 + 오케스트레이터 (완료, 5-a절)
6. 3색 판정(`EXTRA_REQUEST`) → `Requirement` 9상태 합의 흐름 연결 (완료)
7. 체크리스트·답변 초안 생성 API (완료, 발송은 아직 없음)
8. 증분 worker(Slack Events·Gmail historyId)·감사 로그 (보류)

공개 필드·API는 `PRODUCT_API_DESIGN.md`와 Swagger를 함께 갱신한다.
