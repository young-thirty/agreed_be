# Agreed 도메인 명세와 정책 결정

> 프로젝트·티켓·이벤트의 관계, 메시지가 들어와 티켓이 되기까지의 분기,
> 그리고 티켓 하나에 붙는 AI 솔루션 패키지를 정의한다.
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

**LLM은 MongoDB를 직접 수정하지 않는다.** 구조화된 판단만 반환하고, 백엔드가
검증한 뒤 허용된 변경만 적용한다. 근거 인용이 원문에 실제로 없으면 그 판단은
버린다.

---

## 2. 세 가지 상태

이름이 비슷해 가장 많이 섞이는 지점이다.

| 필드 | 붙는 곳 | 값 |
|---|---|---|
| `projectStatus` | 프로젝트 | `draft` `active` `completed` `rejected` |
| `projectClassification` | 이벤트 | `draft` `active` `none` |
| `ticketStatus` | 티켓 | `active` `done` `rejected` |

`projectClassification`은 메시지가 도착한 시점의 스냅샷이다. 나중에 프로젝트가
`completed`가 되어도 그때 저장된 이벤트의 분류는 `active` 그대로 남는다.

### 2.1 티켓 상태

```text
active (기본)  →  done      사람이 대응을 끝냈다
              →  rejected  받지 않기로 했다
```

**티켓은 항상 `active`로 생성된다.** `pending`은 두지 않는다 — `active`와의 경계가
사람마다 달라 아무도 쓰지 않는 상태가 되기 때문이다.

**상태 전이는 사람만 한다. AI는 제안조차 하지 않는다.** "이 요청에 대응이
끝났는가"는 메시지만 봐서 알 수 없다. 프리랜서가 실제로 작업했는지, 클라이언트가
납득했는지는 대화 밖에서 일어난다. 자동화하면 열려 있어야 할 티켓이 닫히고,
그게 곧 놓친 요청이 된다.

### 2.2 티켓 생성 규칙

**티켓은 Inbound로만 생성된다.** Outbound는 기존 티켓을 업데이트할 수 있지만
새 티켓을 만들 수 없다.

근거: 티켓은 "클라이언트가 요청한 것"이다. 프리랜서가 먼저 보낸 메일로 티켓이
생기면, 내가 제안한 것이 고객 요구사항으로 기록되어 계약 근거가 뒤집힌다.

---

## 3. 처리 우선순위

```text
1. 코드로 확정할 수 있는 값
2. DB 조회로 확정할 수 있는 값
3. LLM 판단이 필요한 값
4. 확신이 부족하면 manual_review
```

---

## 4. 인바운드 → 티켓 매칭

새 인바운드가 들어오면 **기존 티켓에 붙일지, 새 티켓을 만들지**를 정해야 한다.

### 4.1 방식: 임베딩 없이 컨텍스트로 푼다

```text
1. 이 프로젝트의 active 티켓을 최근 순으로 최대 20개 가져온다
2. 각 티켓에서 id · title · summary만 뽑는다 (본문은 넣지 않는다)
3. 새 인바운드 원문 + 티켓 목록을 한 번의 LLM 호출에 넣는다
4. LLM이 ticketId 하나를 고르거나 null(새 티켓)을 반환한다
5. 후보 목록 밖의 id를 내면 무효로 보고 새 티켓으로 처리한다
```

**근거.** 벡터 DB와 임베딩 파이프라인을 두지 않는다. 한 프로젝트의 열린 티켓은
현실적으로 수십 개를 넘지 않고, 제목만 넣으면 20개라도 컨텍스트가 1천 토큰
안쪽이다. 인덱스를 만들고 갱신하는 비용이 이득보다 크다.

`summary`까지만 넣고 본문을 넣지 않는 것도 같은 이유다. "이 요청이 저 티켓과
같은 얘기인가"는 제목 수준에서 대부분 갈린다.

상한 20개를 넘으면 최근 것만 본다. 오래된 티켓과 새 요청이 같은 건인 경우는
드물고, 놓쳐도 새 티켓이 하나 더 생길 뿐 데이터가 깨지지 않는다.

### 4.2 프로젝트 후보 선택

같은 클라이언트 이메일이 여러 프로젝트에 등록되어 있을 수 있다.

**결정. 후보가 하나여도 항상 LLM에 묻는다.**

```text
1. clientEmails에 해당 주소가 있는 프로젝트를 찾는다
2. completed·rejected는 후보에서 뺀다 (7절에서 따로 처리)
3. 후보 목록 + 각 프로젝트의 이름·기간·진행 중인 티켓 제목을 LLM에 넘긴다
4. LLM이 projectId 하나를 고른다
5. 후보 목록 밖의 값을 내거나 고르지 못하면 manual_review
```

**근거.** 이메일 주소가 같다고 그 프로젝트 얘기인 것은 아니다. 같은 클라이언트가
다른 건으로 연락하거나, 계약과 무관한 안부 메일을 보내는 경우가 흔하다. 주소만
보고 코드가 확정하면 그런 메시지가 전부 프로젝트에 붙는다.

LLM은 "이 메시지가 이 프로젝트 얘기인가"를 함께 판단하므로, 후보가 하나여도
물어보는 편이 오귀속을 막는다. 호출 한 번의 비용보다 잘못 붙은 티켓을 사람이
찾아 옮기는 비용이 크다.

---

## 5. Draft → Active/Rejected 전환

**결정. LLM이 판단한다.**

### 5.1 트리거

**두 경우에만 판단이 돈다.**

```text
(1) draft 프로젝트에 inbound 이벤트가 들어올 때
(2) draft 프로젝트에 outbound 이벤트가 들어올 때
```

`active`·`completed`·`rejected` 프로젝트에는 이 판단을 돌리지 않는다. 전환은
`draft`에서 나가는 한 방향뿐이다.

### 5.2 입출력

```json
{
  "transition": "active | rejected | stay",
  "reason": "판단 이유 한두 문장",
  "evidence": [{ "sourceId": "event_id", "quote": "실제 원문 문장" }]
}
```

백엔드가 하는 일:

- `evidence[].quote`가 원문에 실제로 있는지 확인한다 (`core/grounding.py`)
- 없으면 전환하지 않고 `stay`로 떨어뜨린다
- 전환하면 `activatedAt` 또는 `rejectedAt`을 기록한다
- **전환 이력을 남긴다** — 무엇을 근거로 언제 바뀌었는지

### 5.3 되돌릴 수 있게 둔다

`activatedAt`은 이후 모든 범위 판정의 기준선이 된다. "이 요청이 계약 이후에
생겼는가"가 이 시각으로 갈리기 때문에, 하루가 틀리면 범위 밖 요청이 범위 안으로
들어온다.

그래서 전환 자체는 AI가 하되 **이력과 근거를 남겨 사람이 되돌릴 수 있게** 한다.
화면에서 "AI가 8/26 메일을 근거로 Active로 전환했습니다"를 보여주고, 아니면
되돌리는 버튼을 둔다. 자동으로 하되 흔적을 남기지 않는 것이 가장 나쁘다.

**계약 반영(`apply_to_contract`)은 여전히 사람만 한다.** 금액이 바뀌는 지점은
전환과 성격이 다르다.

---

## 6. 티켓 솔루션 패키지

**이 제품의 결과물이다.** 티켓 하나에 AI가 붙이는 산출물 묶음이다.

```text
프로젝트
  └ 티켓 (클라이언트 요청 하나)
      ├ 조언 메시지        무엇을 어떻게 할지
      ├ 조언 이유          왜 그렇게 보는지
      ├ 근거 조문          계약서·제안서의 실제 인용
      ├ 관련 파일          이 판단에 쓰인 자료
      └ 답변 초안          스타일별로 골라 쓰는 회신 문안
```

### 6.1 두 단계로 나눈다

```text
POST /api/tickets/{id}/solution        조언 + 이유 + 근거 + 파일   (한 번, 저장)
POST /api/tickets/{id}/reply-draft     초안 하나                    (스타일마다 호출)
```

**근거.** 조언과 근거는 티켓당 한 번 만들면 되고 바뀌지 않는다. 저장해 두고 화면
진입 때마다 다시 만들지 않는다.

반면 초안은 사람이 스타일을 바꿔가며 여러 번 본다. 모든 스타일을 미리 만들면
쓰지도 않을 초안에 토큰을 쓴다. 고른 스타일 하나만 그때 만든다.

### 6.2 근거 조문과 파일

```json
{
  "contractBasis": [
    { "quote": "영문 페이지는 범위에 포함되지 않는다", "documentId": "...", "documentName": "계약서 v1" }
  ],
  "relatedFiles": [
    { "materialId": "...", "fileName": "제안서.pdf", "documentType": "PROPOSAL" }
  ]
}
```

`quote`는 코드가 실제 문서와 다시 대조한다. 지어낸 인용이면 그 근거를 버리고,
남은 근거가 없으면 조언에 "확인 가능한 근거가 부족합니다"를 붙인다.

계약 대조는 이미 구현된 서브 에이전트(`infra/llm/subagents/contract_match.py`)가
`read_contract`·`search_materials` 도구로 수행한다. 솔루션 패키지는 그 결과를
사람이 읽을 문장으로 바꾸는 층이다.

### 6.3 답변 초안 스타일

스타일 목록은 **프론트가 정한다.** 백엔드는 문자열 키를 받고 톤 지시문을
매핑한다.

```python
REPLY_STYLES = {
    "plain":      "담백하고 사무적으로",
    "polite":     "정중하고 완곡하게",
    "witty":      "가볍고 친근하게, 다만 사안은 정확하게",
    "firm":       "선을 분명히 긋되 예의는 지키며",
}
```

**등록된 키만 받는다.** 모르는 키는 400으로 거절한다. 스타일을 늘리는 것은
이 dict에 한 줄 추가하는 일이라 스키마를 고칠 필요가 없다.

**근거.** 현재 코드는 `Tone = Literal["friendly","professional","concise","firm"]`로
박혀 있어 스타일을 하나 늘릴 때마다 타입과 스키마를 함께 고쳐야 한다. 목록이
아직 확정되지 않았으므로 열어 둔다.

스타일이 바꾸는 것은 **말투뿐**이다. 무엇을 말할지 — 받아들일지, 금액을 부를지 —
는 사람이 정한 값이 정하고, 스타일은 거기에 손대지 않는다.

### 6.4 초안이 하지 않는 것

- 금액과 날짜를 지어내지 않는다. 사람이 확정하지 않았으면 "확인 후 안내드리겠습니다"
- 수락이나 거절을 단정하지 않는다
- **발송하지 않는다.** 생성까지만이고 보내는 것은 사람이 복사해서 보낸다

---

## 7. 나머지 정책

### 7.1 confidence 기준

| confidence | 후보 | 동작 |
|---|---|---|
| `≥ 0.85` | 1개 | 자동 적용 |
| `0.60 ~ 0.85` | 1개 | 적용하되 `needsReview: true` — 화면에 확인 배지 |
| `< 0.60` | 무관 | `manual_review` |
| 무관 | 2개+ | `manual_review` |

confidence가 아무리 높아도 자동으로 하지 않는 것:

- **계약 반영** (`apply_to_contract`)
- **티켓을 `done`·`rejected`로 바꾸기** (2.1)

근거: confidence는 모델의 자기보고이지 통계적 신뢰구간이 아니다. 0.9가 90%
정확을 뜻하지 않는다. 그래서 "높으면 믿는다"가 아니라 **"틀렸을 때 되돌릴 수
있는가"**로 선을 긋는다. 프로젝트 연결은 화면에서 옮기면 그만이고, 계약 버전
N+1은 되돌려도 이력에 남는다.

### 7.2 manual_review

별도 화면을 만들지 않고 대시보드의 **`확인 필요` 카운터에 합친다.**

```text
GET  /api/review-queue          processingStatus == manual_review
POST /api/events/{id}/resolve   { projectId?, ticketId?, action }
```

근거: 전용 화면을 만들면 아무도 안 들어간다. 사람이 매일 보는 숫자에 섞어야
처리된다.

### 7.3 외주와 무관한 메일

`projectClassification = none`인 이벤트는 **본문을 저장하지 않는다.**

| 저장한다 | 저장하지 않는다 |
|---|---|
| `externalMessageId`, `occurredAt` | `bodyText`, `subject` 전문, `attachments` |
| 판단 근거 인용 1건 (200자 이내) | |

TTL 인덱스로 30일 뒤 자동 삭제한다.

근거: "이미 봤고 무관했다"는 사실이 없으면 polling 때마다 같은 메일을 다시 LLM에
넣는다. 하지만 본문까지 남길 이유는 없다 — 사용자의 사적인 메일이고, 저장하는
순간 유출 사고의 표면적이 된다.

### 7.4 첨부파일 저장소

**S3.** 이미 구현되어 있다(`infra/storage/s3.py`, Terraform 버킷).

```text
키       materials/{ownerId}/{projectId}/{materialId}/{fileName}
MongoDB  메타데이터와 storageKey만
접근     public access 차단, 서버 프록시로만
수명     30일 lifecycle
```

### 7.5 계약 버전과 티켓

**티켓 하나가 계약 버전 하나를 만든다.** 여러 티켓을 묶어 한 버전으로 반영하지
않는다.

```text
ContractVersion N+1 . appliedTicketId
Ticket . appliedContractVersion
```

모든 티켓이 계약 변경으로 가는 것은 아니다. 대부분은 솔루션 패키지(6절)를 보고
답장하면 끝난다. 계약 밖 변경이라고 판정되고 사람이 합의까지 올린 티켓만
`apply`를 탄다.

근거: 묶으면 "어느 요청 때문에 금액이 300만 원 올랐는가"를 되짚을 수 없다.
계약 분쟁이 도메인인 제품에서 이 추적이 끊기면 존재 이유가 사라진다. 버전 번호가
커지는 것은 문제가 아니다 — 화면에는 "3차 변경: 영문 페이지 추가 (+50만 원)"처럼
티켓 제목으로 보여주면 된다.

멱등성은 `(ownerId, projectId, appliedTicketId)` unique 인덱스가 보장한다.

### 7.6 Slack 채널 공유

**불가. 채널 하나 = 프로젝트 하나.** `UNIQUE(ownerId, slackConnection.channelId)`

근거: 허용하면 Slack 메시지마다 프로젝트를 LLM이 판단해야 하는데, Slack 메시지는
짧고 맥락이 없다. "이거 언제까지 되나요?" 한 줄로 프로젝트를 맞히는 것은 사실상
불가능하다. 제약을 두면 `channelId → projectId`가 코드로 확정된다(3절 우선순위 1).

Gmail은 한 주소가 여러 프로젝트에 걸칠 수 있어 4.2의 LLM 판단이 필요하지만,
Slack은 채널이라는 명확한 경계가 있어 그럴 필요가 없다.

### 7.7 Completed 프로젝트에 새 메시지

이벤트는 저장하고 연결하되 **티켓을 만들지 않고 사람에게 묻는다.**

```text
projectClassification = active   (그 프로젝트 단계에서 온 것이므로)
ticketId = null
suggestedAction = manual_review
→ "완료된 프로젝트에 새 요청이 들어왔습니다"
→ 다시 active로 | 새 프로젝트 만들기 | 무시
```

근거: 하자보수(원래 범위, 프로젝트를 다시 열어야 함)와 추가 발주(새 계약)는
겉으로 구분되지 않는다. 자동으로 티켓을 만들면 완료 프로젝트가 계속 열려 정산이
끝나지 않고, 무시하면 추가 발주를 놓친다 — 프리랜서에게는 그게 매출이다.

---

## 8. LLM 출력 계약

자유 문장이 아니라 JSON만 반환한다. 백엔드가 검증하는 것:

- `evidence[].quote`가 실제 원문·문서에 **존재하는지**
- `projectId`·`ticketId`가 백엔드가 준 **후보 목록 안**의 값인지
- `suggestedAction`이 허용된 값인지
- `confidence`가 7.1의 어느 구간인지

하나라도 어긋나면 적용하지 않고 `manual_review`로 보낸다.

---

## 9. 현재 구현과의 차이

### 9.1 이름만 다른 것

| 명세 | 현재 코드 |
|---|---|
| `communication_events` | `models/source_message.py` |
| `tickets` | `models/client_request.py` |
| `attachments` | `models/project_material.py` |
| `source_connections` | `models/source_link.py` |
| `contract_versions` | `models/contract.py` — 버전마다 새 문서 |

### 9.2 고쳐야 하는 것

| 항목 | 현재 | 필요한 일 |
|---|---|---|
| `ticketStatus` | `WAITING`/`COMPLETED` 2종 | `active`/`done`/`rejected` 3종 |
| 티켓 매칭 | 없음 (메시지 1건 = 티켓 1건) | 4.1 컨텍스트 매칭 |
| 프로젝트 후보 판단 | 없음 (source-link가 지정) | 4.2 LLM 선택 |
| Draft 전환 판단 | 없음 | 5절 |
| 솔루션 패키지 | 조언·이유·파일 없음 | 6절 |
| 초안 스타일 | `Tone` Literal 4종 고정 | 6.3 레지스트리 |
| `projectStatus = rejected` | 없음 | enum 추가 |
| `projectClassification` | 없음 | 필드 추가 |
| `manual_review` 큐 | 없음 | 7.2 |

### 9.3 이미 되어 있는 것

- 3색 판정과 근거 재검증 (`core/grounding.py`)
- 계약 반영 승인 게이트 (`apply_to_contract`)
- S3 첨부 저장, Git 저장소 탐색 서브 에이전트
- 확인 질문·답변 초안 생성 (스타일은 4종 고정)

---

## 10. 이행 순서

```text
1단계  ticketStatus 3종 확장          WAITING→active, COMPLETED→done, rejected 추가
2단계  티켓 매칭(4.1)을 sync에 넣는다  메시지 1건 = 티켓 1건 고정을 푼다
3단계  솔루션 패키지(6절)             조언·이유·근거·파일을 한 번에 만들어 저장
4단계  초안 스타일 레지스트리(6.3)     Literal을 dict로 바꾼다
5단계  프로젝트 후보 판단(4.2) + Draft 전환(5절)
6단계  manual_review 큐(7.2), projectClassification
```

1~4단계까지가 "티켓을 열면 조언과 초안이 보인다"는 화면을 완성한다.
5~6단계는 자동 수집이 붙을 때 필요하고, 그 전까지는 source-link가 프로젝트를
지정하므로 없어도 동작한다.
