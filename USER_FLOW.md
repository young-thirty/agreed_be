# Agreed 사용자 흐름과 설계 근거

> 회원가입부터 계약 반영까지, 화면이 어떤 API를 부르고 왜 그렇게 나눴는지 적는다.
> 화면 코드는 `young-thirty/agreed_dev`(Next.js), API는 이 저장소다.
> 확정 DTO는 [PRODUCT_API_DESIGN.md](./PRODUCT_API_DESIGN.md), 에이전트 구조는
> [AI_AGENTS.md](./AI_AGENTS.md)에 있다.

이 문서는 **2026-08-26 기준 실제 코드를 대조해** 썼다. 아직 연결되지 않은 구간은
숨기지 않고 6절에 모아 뒀다.

---

## 1. 전체 흐름

```text
회원가입 / 로그인
   → 연동 (Gmail · Slack · 파일 · 직접 입력)
   → 대시보드 (프로젝트 목록)
   → 새 프로젝트 (Draft로 생성)
   → 프로젝트 시작 (Draft → Active)
   → 요청 분석 · 요구사항 타임라인
   → 사람 승인 → 계약 반영
```

**Draft와 Active를 나눈 것이 이 흐름의 뼈대다.** 계약이 체결되기 전에는 분석할
기준(계약 범위)이 없다. 기준 없이 요청을 판정하면 전부 "확인 필요"로 나와 화면이
의미를 잃는다. 그래서 프로젝트는 Draft로 만들고, 사람이 "계약이 체결됐다"고
선언하는 순간(`프로젝트 시작`)부터 분석이 시작된다.

---

## 2. 회원가입과 로그인

### 백엔드가 하는 일

```text
POST /api/auth/signup   { name, email, password, phoneNumber }
POST /api/auth/login    { email, password }
POST /api/auth/logout
GET  /api/auth/me
```

성공하면 `agreed_session` **HttpOnly 쿠키**가 발급된다. 브라우저에는 무작위
opaque 토큰만 가고, MongoDB에는 그 토큰의 SHA-256 해시만 저장한다. 비밀번호는
Argon2 해시만 남는다.

### 근거: 왜 소셜 로그인이 아닌가

Google·Slack OAuth를 로그인으로 재사용하지 않는다. 그 둘은 **로그인한 사용자가
외부 채널 접근을 허용하는 별도 연동**이다. 섞으면 두 가지가 깨진다.

- Gmail 연동을 해제하면 로그인까지 풀린다
- Slack만 쓰는 사용자는 Gmail 없이 가입할 수 없다

로그인과 연동은 수명이 다른 권한이라 분리한다.

### 근거: 왜 JWT가 아니라 서버 세션인가

provider token(Google refresh token, Slack bot token)을 서버가 쥐고 있어야 한다.
세션을 무효화할 수 없으면 토큰이 새어도 회수할 방법이 없다. 서버 측 opaque
세션은 DB에서 지우면 즉시 끊긴다.

---

## 3. 연동 화면 (이미지 1)

네 가지 입력 경로를 한 화면에 모은다.

| 카드 | 상태 | 호출 |
|---|---|---|
| **Gmail** | `Connected · 주소` | `GET /api/email/status` → `GET /api/email/connect`(브라우저 이동) |
| **Slack** | `Connected` + 워크스페이스·채널 목록 | `GET /api/slack/connect` → `POST /api/slack/workspaces` → `/channels` → `/join` → `/messages` → `/thread` |
| **파일 업로드** | 사용 가능 | (미연결, 6절) |
| **직접 입력** | 사용 가능 | `POST /api/analyze` |

### 근거: OAuth 시작만 GET인 이유

응답 규약은 POST + JSON이지만 OAuth 시작·callback은 예외로 GET을 쓴다.
브라우저가 provider로 **이동**해야 하므로 fetch로는 불가능하다.

```ts
window.location.href = apiUrl('/api/email/connect');
```

callback은 로그인 세션에 묶인 난수 `state`를 검증하고 한 번 쓴 뒤 폐기한다.
이게 없으면 다른 사이트가 사용자 계정에 자기 Gmail을 붙일 수 있다(CSRF).

### 근거: Slack 파일에 url_private을 안 내리는 이유

Slack의 `url_private`은 bot token이 있어야 열린다. 프론트에 그대로 내리면
토큰도 함께 내려야 한다. 대신 `fileId`만 주고 `GET /api/slack/file`이 서버에서
`files.info`로 실제 주소를 다시 조회해 프록시한다.

### 근거: 연동 화면의 조회와 프로젝트 저장은 다른 API다

이 화면의 Gmail·Slack 조회는 **연결이 살아 있는지 확인**하는 용도다. provider를
직접 읽고 Mongo에 저장하지 않는다. 프로젝트에 귀속된 원문 저장은
`/projects/{id}/source-links` + `/sync`가 담당한다.

둘을 합치지 않은 이유는 소유권 때문이다. 연동은 사용자 단위, 원문은 프로젝트
단위다. "이 메일이 어느 프로젝트 것인가"는 사람이 정해야 하는 판단이라,
연결만으로 자동 귀속시키지 않는다.

---

## 4. 대시보드 (이미지 2)

`GET /api/projects?status=&sort=` → `ProjectSummary[]`

### 상단 집계 5칸

전체 / 진행 중 / 확인 필요 / 진행 중 / 마감 임박(7일).

**`확인 필요`는 `unansweredRequestCount`다.** `responseStatus == "WAITING"`인
요청 수를 프로젝트마다 센다.

### 근거: 이 수를 Project에 저장하지 않는 이유

중복 저장하면 요청이 늘거나 대응 완료로 바뀔 때마다 두 문서를 함께 고쳐야 하고,
한쪽만 실패하면 화면 숫자가 조용히 틀어진다. `(ownerId, projectId, responseStatus)`
복합 인덱스로 셀 수 있으므로 매번 센다.

### 검색·필터·정렬

- 검색: 프로젝트명·클라이언트명
- 상태: `ACTIVE` / `DRAFT` / `COMPLETED`
- 정렬: 확인 필요 순 / 마감 임박 순 / 예산 높은 순 / 이름순

백엔드가 받는 `sort`는 `status`(기본) / `updatedAt` / `createdAt` 셋뿐이다.
화면의 네 가지 정렬은 **프론트가 받아온 배열을 다시 정렬**한다. 시연 MVP는
페이지네이션 없이 전체 배열을 주므로 이 방식이 성립한다.

기본 정렬 `status`는 `ACTIVE(0) → DRAFT(1) → COMPLETED(2)`, 동률이면 `updatedAt`
내림차순이다. 이 순서를 `statusRank`로 저장해 인덱스를 태운다.

---

## 5. 프로젝트 생성과 시작 (이미지 3·4)

### 새 프로젝트 (이미지 3)

`POST /api/projects` — 이름 / 클라이언트 이름·메일 / 간단 설명 / 시작·종료일 /
계약 금액. 전부 선택이고 이름과 클라이언트 이름만 필수다.

> 새 프로젝트는 Draft로 생성됩니다. 계약이 체결되면 워크스페이스에서
> '프로젝트 시작'을 누르세요.

**클라이언트 메일이 핵심 필드다.** 이 주소가 Gmail 동기화의 필터가 된다.
없어도 프로젝트는 만들어지지만 메일 분석을 쓸 수 없어서, 화면에도 그렇게 적혀 있다.

### 근거: 왜 금액·일정이 필수가 아닌가

계약 협상 중에 프로젝트를 먼저 만드는 경우가 많다. 필수로 두면 아직 정해지지
않은 숫자를 지어내 넣게 되고, 그 숫자가 나중에 계약 근거로 쓰인다. 비워 두는
편이 정직하다. 화면은 `금액 미정`으로 표시한다.

### 프로젝트 시작 (이미지 4)

`PATCH /api/projects/{id}/status` → `ACTIVE`

Draft 상태에서는 요청 분석 탭이 비어 있고 안내 문구와 시작 버튼만 나온다.

### 근거: 이 전이를 AI에게 맡기지 않는 이유

"계약이 체결됐는가"는 추론이 아니라 사실 확인이다. 메일에 "계약서 보냈습니다"가
있다고 체결된 게 아니다. 사람이 누르는 버튼 하나로 둔다.

---

## 6. 계약 반영까지 — 사람이 쥐는 스위치

```text
요청 도착 → AI 3색 판정 → 빨강만 요구사항 카드로 승격
   → 사람이 상태를 올린다 (요청 → 제안 → 합의)
   → POST /projects/{id}/contract/apply
   → 계약 버전 N+1
```

계약을 바꾸는 통로는 `core/contract_ops.py`의 `apply_to_contract` 하나뿐이고,
그 안에 `status != "합의"` 검사가 있다. **이 검사를 우회하는 경로를 만들지 않는다.**

| 판정 | 색 | 요구사항 승격 |
|---|---|---|
| `IN_SCOPE_ACTION_REQUIRED` | 초록 | 안 함 — 계약 안에서 처리하면 되는 일 |
| `OUT_OF_SCOPE_COORDINATION_REQUIRED` | 주황 | 안 함 — 아직 판단할 거리가 아님 |
| `EXTRA_REQUEST` | 빨강 | **함** — 계약 밖 변경 근거가 분명 |

계약은 버전마다 새 문서를 만든다. 갱신이 아니라 추가라 이전 버전이 남고,
"어떤 요구사항 때문에 이 버전이 생겼는지"를 `appliedRequirementId`로 추적한다.
계약 분쟁이 도메인이라 이 이력이 제품의 결과물 자체다.

---

## 7. 아직 연결되지 않은 구간

화면이 실제로 부르는 API를 코드에서 세어 확인한 결과다. 시연 시나리오를 짤 때
이 목록을 먼저 본다.

### 7-1. 회원가입 화면이 백엔드 세션을 만들지 않는다

프론트 `/signup`은 프로필을 **localStorage에만** 저장하고
`POST /api/auth/signup`을 부르지 않는다. 그런데 `/api/projects`를 비롯한 모든
프로젝트 API는 `agreed_session` 쿠키를 요구한다.

지금 화면이 도는 것은 같은 브라우저에 이미 세션 쿠키가 있기 때문이다
(Swagger 로그인 또는 `POST /api/auth/demo-session`). **다른 브라우저·시크릿 창에서
열면 프로젝트 목록이 401로 비어 보인다.**

시연 전에 둘 중 하나를 해야 한다.
- 프론트 회원가입/로그인을 `/api/auth/*`에 연결한다 (권장)
- 시연 브라우저에서 미리 로그인해 둔다

### 7-2. 채널 원문이 프로젝트에 저장되지 않는다

연동 화면의 Gmail·Slack 조회는 provider를 직접 읽을 뿐 `SourceMessage`로
저장하지 않는다. 그래서 **AI 요청 판정 파이프라인이 아직 화면과 이어지지 않았다.**

이으려면 프로젝트마다 한 번씩:

```text
POST /projects/{id}/source-links          # Gmail 상대 또는 Slack 채널 등록
POST /projects/{id}/source-links/{sid}/sync   # 원문 저장 + 분석 시작
GET  /projects/{id}/requests              # 3색 판정 카드
```

현재 화면의 요청 분석은 `POST /api/analyze`(붙여넣기 경로)를 쓴다. 두 파이프라인은
서로 다르다 — `/analyze`는 요구사항 9상태를 뽑고, sync 경로는 3색 판정을 만든다.

### 7-3. 백엔드에 있으나 화면이 아직 안 쓰는 API

| API | 용도 |
|---|---|
| `GET /projects/{id}/materials` | 자료 탭 |
| `GET /requests/{id}` | 요청 상세(원문·근거) |
| `POST /requests/{id}/checklist` | 답변 전 확인 항목 |
| `POST /requests/{id}/reply-draft` | 답변 초안 |
| `PATCH /requests/{id}/response-status` | 대응 완료 처리 |
| `GET·POST /projects/{id}/contract`, `/apply` | 계약 조회·등록·반영 |
| `POST /projects/{id}/git/ask` | 저장소 코드 질문 |
| `POST /api/github/connect` | GitHub PAT 등록 |

### 7-4. 파일 업로드 카드가 동작하지 않는다

연동 화면의 `파일 업로드`는 `사용 가능`으로 표시되지만 업로드 endpoint가 없다.
현재 `ProjectMaterial`은 Slack 첨부에서만 만들어진다. 원본은 S3에 저장되지만
(`storageKey`), 텍스트 추출(OCR·PDF 파싱)은 아직 없어 자료 분류는 파일명
휴리스틱으로 떨어진다.
