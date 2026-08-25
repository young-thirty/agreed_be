# Agreed 도메인 명세와 미결정 항목 설계안

> 프로젝트·티켓·이벤트의 관계, 메시지가 들어와 티켓이 되기까지의 분기,
> 그리고 아직 정하지 않았던 정책 9건의 설계안을 담는다.
>
> 화면 흐름은 [USER_FLOW.md](./USER_FLOW.md), 에이전트 구조는
> [AI_AGENTS.md](./AI_AGENTS.md), 현재 확정 DTO는
> [PRODUCT_API_DESIGN.md](./PRODUCT_API_DESIGN.md)에 있다.

---

## 1. 관계

서비스 사용자는 **프리랜서**다.

```text
Inbound   클라이언트 → 프리랜서
Outbound  프리랜서 → 클라이언트
채널      Gmail, Slack (카카오톡은 공개 API가 없어 지원하지 않는다)
```

LLM이 보조하는 판단은 다섯 가지다.

- 외주 관련 메시지인가
- 어느 프로젝트에 속하는가
- 기존 티켓과 관련 있는가
- 새 티켓을 만들어야 하는가
- Draft 프로젝트를 Active 또는 Rejected로 전환할 만한가

**LLM은 MongoDB를 직접 수정하지 않는다.** 구조화된 판단만 반환하고, 백엔드가
검증한 뒤 허용된 변경만 적용한다.

---

## 2. 세 가지 상태를 헷갈리지 않기

이름이 비슷해서 가장 많이 섞이는 지점이다.

| 필드 | 붙는 곳 | 값 | 뜻 |
|---|---|---|---|
| `projectStatus` | 프로젝트 | `draft` `active` `completed` `rejected` | 프로젝트 **자체**의 현재 상태 |
| `projectClassification` | 이벤트 | `draft` `active` `none` | 이 **메시지**가 연결된 프로젝트 단계 |
| `ticketStatus` | 티켓 | `active` `pending` `done` `rejected` | 티켓 **자체**의 현재 상태 |

`projectClassification`은 메시지가 도착한 시점의 스냅샷이다. 나중에 프로젝트가
`completed`가 되어도 그때 저장된 이벤트의 분류는 `active` 그대로 남는다.
이력이 흔들리지 않게 하려는 것이다.

### 티켓 생성 규칙

**티켓은 Inbound로만 생성된다.** Outbound는 기존 티켓을 업데이트할 수 있지만
새 티켓을 만들 수 없다.

근거: 티켓은 "클라이언트가 요청한 것"이다. 프리랜서가 먼저 보낸 메일로 티켓이
생기면, 내가 제안한 것이 고객 요구사항으로 기록되어 계약 근거가 뒤집힌다.

---

## 3. 처리 우선순위

```text
1. 코드로 확정할 수 있는 값      ← 가장 먼저, 가장 싸다
2. DB 조회로 확정할 수 있는 값
3. LLM 판단이 필요한 값
4. 확신이 부족하면 manual_review
```

이 순서가 설계 전체를 지배한다. 8절의 Slack 채널 제약도, 5절의 confidence 구간도
전부 "LLM에게 물어볼 것을 줄인다"는 같은 목적에서 나왔다.

---

## 4. 미결정 항목 설계안

명세 12절이 남긴 9건이다. 각각 **결정 / 근거** 순으로 적는다.

---

### 4.1 같은 클라이언트 이메일이 여러 프로젝트에 있을 때

**결정.** 백엔드가 후보를 좁히고, LLM은 그 후보 중에서만 고른다.

```text
1. clientEmails에 해당 주소가 있는 프로젝트를 전부 찾는다
2. completed·rejected는 후보에서 뺀다 (4.9는 예외로 따로 처리)
3. 후보가 1개  → 코드가 확정한다. LLM에게 묻지 않는다
4. 후보가 2개+ → active를 draft보다 앞에 두고, 각각의 최근 이벤트 시각과
                 진행 중인 티켓 제목을 함께 LLM에 넘긴다
5. LLM confidence < 0.8 이거나 후보를 못 고르면 manual_review
```

**LLM은 백엔드가 준 후보 ID 목록 밖의 값을 낼 수 없다.** 낸다면 스키마 검증에서
걸러 `manual_review`로 보낸다.

**근거.** 잘못 귀속되면 티켓이 엉뚱한 계약에 붙고, 그 티켓이 나중에 계약 변경
근거가 된다. 계약 분쟁이 도메인이라 오귀속 비용이 "한 번 더 묻는" 비용보다
훨씬 크다. 그리고 후보가 1개일 때 LLM을 부르지 않는 것만으로 호출의 대부분이
사라진다 — 실제로 한 클라이언트가 프로젝트 여러 개를 동시에 주는 경우는 드물다.

---

### 4.2 Draft → Active/Rejected 전환

**결정.** 자동화하지 않는다. **AI는 제안만 하고 사람이 누른다.**

```text
LLM이 review_transition을 제안
  → project.pendingTransition에 기록만 한다
     { to: "active" | "rejected", reason, evidence[], suggestedAt }
  → 화면에 배너: "계약이 체결된 것 같습니다. 확인해 주세요"
  → 사람이 누르면 PATCH /projects/{id}/status 로 전환
  → 무시하면 pendingTransition은 다음 제안으로 덮인다
```

**근거.** CLAUDE.md의 제품 원칙 그대로다 — "계약이 체결됐는가"는 사실 확인이지
추론이 아니다. 실무에서도 "계약서 보냈습니다"와 "계약 체결됐습니다"는 다르고,
날인·입금까지 가야 확정되는 경우가 많다.

그리고 `activatedAt`이 **계약 이력의 기준 시각**이 된다. 이후 모든 요구사항
변경은 "계약 이후에 발생했는가"로 판정되므로, 이 시각이 하루만 틀려도 범위 밖
요청이 범위 안으로 들어온다. AI가 정할 값이 아니다.

---

### 4.3 confidence 자동 적용 기준

**결정.** confidence로 나누되, **되돌릴 수 있는 것만** 자동으로 한다.

| confidence | 후보 | 동작 |
|---|---|---|
| `≥ 0.85` | 1개 | 자동 적용 |
| `0.60 ~ 0.85` | 1개 | 적용하되 `needsReview: true` — 화면에 확인 배지 |
| `< 0.60` | 무관 | `manual_review`. 아무것도 바꾸지 않는다 |
| 무관 | 2개+ | `manual_review`. 임의 선택 금지 |

**confidence가 아무리 높아도 자동으로 하지 않는 것:**

- 프로젝트 상태 전환 (4.2)
- 계약 반영 (`apply_to_contract`)
- 티켓을 `done` 또는 `rejected`로 바꾸기

**근거.** confidence는 모델의 자기보고이지 통계적 신뢰구간이 아니다. 0.9가
90% 정확을 뜻하지 않는다. 그래서 "높으면 믿는다"가 아니라 **"틀렸을 때 되돌릴
수 있는가"**로 선을 긋는다.

이벤트를 프로젝트에 연결한 것은 화면에서 옮기면 그만이다. 계약 버전 N+1을
만든 것은 되돌려도 이력에 남는다. 이 둘을 같은 기준으로 다룰 수 없다.

---

### 4.4 manual_review 화면과 처리

**결정.** 별도 화면을 만들지 않는다. 대시보드의 **`확인 필요` 카운터에 합친다.**

```text
GET  /api/review-queue          processingStatus == manual_review 인 이벤트
POST /api/events/{id}/resolve   { projectId?, ticketId?, action }
       action: link | create_ticket | create_project | discard
```

각 항목은 원문, AI가 좁힌 후보들, 판단 근거 인용을 함께 보여준다.

**근거.** manual_review 전용 화면을 만들면 아무도 안 들어간다. 이미 사람이 매일
보는 "확인 필요" 숫자에 섞어야 처리된다. 큐가 비어 있으면 화면에 아무것도 안
보이므로 UI 부담도 없다.

---

### 4.5 외주와 무관한 개인 메일

**결정.** `projectClassification = none`인 이벤트는 **본문을 저장하지 않는다.**

| 저장한다 | 저장하지 않는다 |
|---|---|
| `externalMessageId`, `occurredAt` | `bodyText` |
| `aiDecision.outsourcingRelated = false` | `subject` 전문 |
| 판단 근거 인용 1건 (200자 이내) | `attachments` |

`expireAfterSeconds` TTL 인덱스로 **30일 뒤 자동 삭제**한다.

**근거.** "이 메시지는 이미 봤고 외주와 무관했다"는 사실은 남아야 한다. 안
남기면 polling 때마다 같은 메일을 다시 LLM에 넣게 되고, 비용과 시간이 계속 든다.

하지만 본문까지 남길 이유가 없다. 사용자의 사적인 메일이고, 저장하는 순간
유출 사고의 표면적이 된다. 중복 방지에 필요한 것은 ID뿐이다.

TTL 인덱스는 이미 `models/session.py`에서 쓰고 있어 새 메커니즘이 아니다.

---

### 4.6 첨부파일 저장소

**결정.** **S3.** 이미 구현되어 있다.

```text
키 형식   materials/{ownerId}/{projectId}/{materialId}/{fileName}
MongoDB   메타데이터와 storageKey만 (fileName, mimeType, sizeBytes)
접근      public access 전면 차단. 서버 프록시로만 내려준다
수명      30일 lifecycle 후 자동 삭제
```

구현: `infra/storage/s3.py`, 버킷·IAM은 `deploy/terraform/main.tf`.

**근거.** 배포가 이미 AWS App Runner라 인스턴스 role로 키 없이 접근된다. 별도
자격증명을 만들고 관리할 필요가 없다. 프론트에 서명 URL을 내리지 않고 서버
프록시를 고른 것은 Slack 파일(`GET /api/slack/file`)에서 이미 검증한 패턴이라
경로가 하나로 통일되기 때문이다.

---

### 4.7 계약 버전과 티켓 요구사항의 연결

**결정.** **티켓 하나 = 계약 버전 하나.** 묶지 않는다.

```text
ContractVersion N+1
  appliedTicketId  : 이 버전을 만든 티켓
Ticket
  appliedContractVersion : 이 티켓이 반영된 버전 (없으면 null)
```

여러 티켓을 모아 한 버전으로 반영하지 않는다.

**근거.** 묶으면 "어느 요청 때문에 금액이 300만 원 올랐는가"를 되짚을 수 없다.
계약 분쟁이 도메인인 제품에서 이 추적이 끊기면 제품의 존재 이유가 사라진다.

버전 번호가 커지는 것은 문제가 아니다. 버전은 사람에게 보이는 값이 아니라
이력의 키다. 화면에는 "3차 변경: 영문 페이지 추가 (+50만 원)"처럼 티켓 제목으로
보여주면 된다.

멱등성은 `(ownerId, projectId, appliedTicketId)` unique 인덱스가 보장한다.
같은 티켓을 두 번 반영해도 버전이 두 개 생기지 않는다.

---

### 4.8 Slack 채널을 여러 프로젝트가 공유할 수 있는가

**결정.** **불가. 채널 하나 = 프로젝트 하나.**

```text
projects: UNIQUE(ownerId, slackConnection.channelId)
```

이미 다른 프로젝트에 연결된 채널을 등록하려 하면 409와 함께
"이 채널은 이미 다른 프로젝트에 연결되어 있습니다"를 돌려준다.

**근거.** 3절의 처리 우선순위가 이유 전부다.

허용하면 Slack 메시지마다 "어느 프로젝트인가"를 LLM이 판단해야 한다. 그런데
Slack 메시지는 짧고 맥락이 없다 — "이거 언제까지 되나요?" 한 줄에서 프로젝트를
맞히는 것은 사실상 불가능하다.

제약을 두면 `channelId → projectId`가 **코드로 확정**된다(우선순위 1). LLM은
"이 요청이 기존 티켓과 관련 있는가"만 판단하면 되고, 이건 훨씬 쉬운 문제다.

한 채널에 두 프로젝트 얘기가 섞이는 상황은 현실에 있지만, 그때는 채널을 나누는
것이 사용자에게도 낫다. 제품이 그렇게 안내한다.

---

### 4.9 Completed 프로젝트에 새 메시지가 올 때

**결정.** 이벤트는 저장하고 연결하되, **티켓을 만들지 않고 사람에게 묻는다.**

```text
이벤트 저장, projectId 연결
projectClassification = active   (그 프로젝트 단계에서 온 것이므로)
ticketId = null
suggestedAction = manual_review
→ 화면: "완료된 프로젝트에 새 요청이 들어왔습니다"
→ 사람이 고른다:
     프로젝트를 다시 active로  |  새 프로젝트 만들기  |  무시
```

**근거.** 완료 후 메시지는 두 가지 중 하나인데 겉으로는 구분이 안 된다.

- **하자보수** — 원래 계약 범위 안이다. 프로젝트를 다시 열어야 한다
- **추가 발주** — 새 계약이다. 새 프로젝트를 만들어야 한다

자동으로 티켓을 만들면 완료된 프로젝트가 계속 열려 정산이 끝나지 않는다.
그렇다고 무시하면 추가 발주를 놓친다 — 프리랜서에게는 이게 매출이다.

둘 다 손해라서, 한 번 묻는 것이 맞다. 완료 프로젝트에 메시지가 오는 빈도는
낮으므로 사용자를 자주 괴롭히지도 않는다.

---

## 5. LLM 출력 계약

자유 문장이 아니라 JSON만 반환한다.

```json
{
  "outsourcingRelated": true,
  "projectId": null,
  "ticketId": null,
  "suggestedAction": "create_project",
  "confidence": 0.91,
  "reason": "신규 홈페이지 제작 견적과 일정 문의",
  "evidence": [
    { "sourceId": "event_id", "quote": "홈페이지 제작 견적과 작업 일정을 문의드립니다." }
  ]
}
```

백엔드가 검증하는 것:

- `evidence[].quote`가 실제 입력이나 계약 문서에 **존재하는지** (`core/grounding.py`)
- `projectId`·`ticketId`가 백엔드가 준 **후보 목록 안**의 값인지
- `suggestedAction`이 허용된 7종인지
- `confidence`가 4.3의 구간 중 어디인지

하나라도 어긋나면 적용하지 않고 `manual_review`로 보낸다.

---

## 6. 현재 구현과의 차이

명세는 목표이고, 아래가 2026-08-26 기준 실제 코드다. 이름이 다른 것과 없는 것을
구분해 둔다.

### 6.1 이름만 다른 것 (개념 일치)

| 명세 | 현재 코드 |
|---|---|
| `communication_events` | `models/source_message.py` (`SourceMessage`) |
| `tickets` | `models/client_request.py` (`ClientRequest`) |
| `ticket.requirementSummary` | `ClientRequest.summaryTitle` |
| `aiDecision.evidence` | `requestEvidence[]`, `documentEvidence[]` |
| `attachments` | `models/project_material.py` (`ProjectMaterial`) |
| `source_connections` | `models/source_link.py` (`ProjectSourceLink`) |
| `contract_versions` | `models/contract.py` — 버전마다 새 문서 |

### 6.2 아직 없는 것

| 항목 | 현재 | 필요한 일 |
|---|---|---|
| `projectStatus = rejected` | `ACTIVE/DRAFT/COMPLETED`만 | enum에 추가 |
| `projectClassification` | 없음 | `SourceMessage`에 필드 추가 |
| `aiDecision.suggestedAction` | 없음 | 7종 enum + 스키마 |
| `confidence` | 없음 | 4.3 구간 판정 |
| `ticketStatus` 4종 | `responseStatus`(WAITING/COMPLETED) 2종 | 확장 |
| `project.setup.missingFields` | 없음 | 자동 생성 프로젝트용 |
| `project.clientEmails[]` | 단수 `clientEmail` | 배열화 |
| Inbound에서 프로젝트 자동 생성 | 수동 생성만 | 4.1 후보 탐색 + `create_project` |
| Outbound 판별 | Gmail만 `direction` 계산 | Slack도 필요 |
| `manual_review` 큐 | 없음 | 4.4 |

### 6.3 명세보다 앞서 있는 것

- 3색 판정(`aiDecisionStatus`)과 근거 재검증은 이미 동작한다
- 계약 반영 승인 게이트(`apply_to_contract`)가 이미 유일 경로다
- S3 첨부 저장(4.6)이 이미 붙어 있다
- Git 저장소 탐색 서브 에이전트는 명세에 없지만 구현되어 있다

---

## 7. 이행 순서

한 번에 갈아엎지 않는다. 화면이 이미 돌고 있어서다.

```text
1단계  SourceMessage에 projectClassification·aiDecision 필드 추가
       (기존 문서는 null. 읽는 쪽이 없으면 영향 없다)

2단계  sync 파이프라인에 4.1 후보 탐색을 넣는다
       후보 1개면 코드가 확정, 2개+면 LLM, 실패하면 manual_review

3단계  ClientRequest.responseStatus를 ticketStatus 4종으로 확장
       WAITING → active, COMPLETED → done 으로 매핑

4단계  projectStatus에 rejected 추가, pendingTransition(4.2) 도입

5단계  manual_review 큐(4.4)와 자동 프로젝트 생성
```

1~2단계까지가 "채널에서 들어온 메시지가 티켓이 된다"는 흐름을 완성한다.
3단계부터는 시연 이후로 미뤄도 화면이 깨지지 않는다.
