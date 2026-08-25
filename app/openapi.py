"""Swagger/OpenAPI 문서 보정.

실행 로직을 바꾸지 않고, JSONResponse를 직접 반환해 FastAPI가
추론하지 못하는 공통 응답·쿠키 인증·redirect·파일 스키마만 문서화한다.
"""

from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


API_DESCRIPTION = """
프리랜서가 계약 이후 클라이언트와 주고받는 대화에서 **새로 생긴 요구사항**을
찾아내고, 계약 변경분을 사람이 승인하도록 돕는 API입니다.

> AI는 무엇이 바뀌었는지 정리하고, 사람은 받아줄지·얼마에·언제까지를 결정합니다.
> 계약을 바꾸는 통로는 `/contract/apply` 하나뿐이고 그 앞에 합의 여부 검사가 있습니다.

---

## 읽는 순서

API가 많아 보이지만 **네 묶음**입니다. 위에서 아래로 한 방향으로 흐릅니다.

```
① 인증        로그인해서 세션 쿠키를 받는다
② 연동        Gmail·Slack·GitHub 접근 권한을 준다        (사용자 단위)
③ 프로젝트    계약 하나를 만들고, 채널을 연결하고, 원문을 가져온다  (프로젝트 단위)
④ 판정·반영   AI가 만든 요청 카드를 사람이 확인하고 계약에 반영한다
```

**②와 ③을 헷갈리기 쉽습니다.**
`/api/email/*`·`/api/slack/*`은 *연결이 살아 있는지 확인*하려고 provider를 직접
읽습니다. DB에 저장하지 않습니다.
프로젝트에 원문을 쌓는 것은 `/api/projects/{id}/source-links` + `/sync`입니다.
분석·판정은 저장된 원문에만 일어납니다.

---

## 최소 시연 경로

```
1. POST /api/auth/login                             세션 쿠키 발급
2. GET  /api/email/status                           Gmail 연결 확인
   (안 되어 있으면 브라우저로 GET /api/email/connect)
3. POST /api/projects                               프로젝트 생성 (DRAFT)
4. PATCH /api/projects/{id}/status  {"status":"ACTIVE"}   프로젝트 시작
5. POST /api/projects/{id}/contract                 최초 계약 등록 (version 1)
6. POST /api/projects/{id}/source-links             Gmail 상대 또는 Slack 채널 등록
7. POST /api/projects/{id}/source-links/{sid}/sync  원문 저장 + AI 분석 시작
8. GET  /api/projects/{id}/requests                 3색 판정 카드 확인
9. GET  /api/projects/{id}/requirements             빨강 판정에서 생긴 요구사항
10. POST /api/projects/{id}/requirements/{rid}/transition   사람이 '합의'로 올림
11. POST /api/projects/{id}/contract/apply          계약 버전 N+1
```

5번을 건너뛰면 대조할 계약이 없어 판정이 전부 주황으로 나옵니다.
7번은 즉시 응답하고 분석은 백그라운드로 돕니다 — 8번을 몇 초 뒤 다시 부르세요.

---

## 세 가지 상태 구분

이름이 비슷해 가장 많이 섞이는 지점입니다.

| 값 | 붙는 곳 | 뜻 |
|---|---|---|
| `status` (`ACTIVE`/`DRAFT`/`COMPLETED`) | 프로젝트 | 프로젝트 자체의 상태 |
| `aiDecisionStatus` (3색) | 요청 카드 | AI가 본 계약 범위 안팎 |
| `responseStatus` (`WAITING`/`COMPLETED`) | 요청 카드 | 사람이 대응했는지 |
| `status` (9종 한국어) | 요구사항 | 합의 진행 단계 |

**`aiDecisionStatus`와 요구사항 `status`는 완전히 다른 값입니다.**
초록 카드가 곧 '합의'라는 뜻이 아닙니다. AI 판정과 사람의 합의는 독립입니다.

### 3색 판정

| 값 | 색 | 기준 |
|---|---|---|
| `IN_SCOPE_ACTION_REQUIRED` | 초록 | 계약·자료에 요청을 뒷받침하는 조항이 있다 |
| `OUT_OF_SCOPE_COORDINATION_REQUIRED` | 주황 | 애매하거나 근거가 부족하다 |
| `EXTRA_REQUEST` | 빨강 | 계약 밖 변경 근거가 분명하다 |

**빨강만** 요구사항 카드로 승격되어 합의 흐름을 탑니다.

---

## 응답 규약

```json
{ "ok": true,  "data": ... }
{ "ok": false, "error": "사용자가 그대로 읽을 한국어 문장" }
```

상태 코드는 정상 200, 잘못된 입력 400, 로그인 필요 401, 없음 404, 충돌 409,
검증 실패 422, 외부 서비스 실패 502, 서버 오류 500입니다.
필드 이름은 프론트와 맞추기 위해 **camelCase**입니다.

예외: OAuth `connect`/`callback`은 브라우저 redirect라 GET이고,
`GET /api/slack/file`은 JSON이 아닌 binary를 돌려줍니다.

---

## Swagger에서 시험하기

1. `POST /api/auth/login`을 먼저 실행합니다.
2. 브라우저가 HttpOnly `agreed_session` 쿠키를 자동 저장합니다.
3. 이후 잠금 표시된 API를 그대로 실행하면 됩니다.

프론트 fetch는 `credentials: "include"`를 씁니다. 서드파티 쿠키 차단이 켜져
있으면 세션 쿠키를 허용해야 합니다.

로그인 화면이 아직 없을 때는 로컬 `.env`에 `DEMO_SESSION_ENABLED=true`를 넣고
`POST /api/auth/demo-session`을 한 번 실행하면 같은 브라우저에 쿠키가 생깁니다.
운영에서는 반드시 `false`로 둡니다.

---

## AI가 하지 않는 일

- 금액·납기·수락 여부를 결정하지 않습니다
- 요구사항 상태를 '합의'·'완료'·'거절'로 제안하지 않습니다 (스키마에서 막힙니다)
- 계약을 직접 수정하지 않습니다
- `mark-sent`는 화면 상태만 저장하고 Gmail·Slack으로 실제 발송하지 않습니다

모델이 만든 인용문은 코드가 원문과 다시 대조하고, 지어낸 인용이면 근거를 버린
뒤 판정을 주황으로 내립니다.

---

## 아직 보류한 범위

실시간 provider 이벤트(Slack Events·Gmail watch), 큐/워커, OCR·텍스트 추출,
페이지네이션·검색, 답장 실제 발송, 수동 파일 업로드는 시연 이후 단계입니다.
도메인 목표 모델과 미결정 정책 설계안은 저장소의 `DOMAIN_SPEC.md`에 있습니다.
""".strip()


OPENAPI_TAGS = [
    {
        "name": "시스템",
        "description": "서버 기동 확인. 배포 health check가 이 경로를 씁니다.",
    },
    {
        "name": "① 인증",
        "description": (
            "이메일·비밀번호 회원가입과 로그인. 성공하면 HttpOnly `agreed_session` "
            "쿠키가 발급되고, 아래 모든 API가 이 쿠키로 소유자를 정합니다. "
            "**요청 body의 사용자 ID를 신뢰하지 않습니다.** "
            "Google·Slack OAuth는 로그인이 아니라 별도 연동입니다(② 참고)."
        ),
    },
    {
        "name": "② 연동 · Gmail",
        "description": (
            "로그인한 사용자가 Gmail 읽기 권한을 주는 단계입니다. "
            "`connect`/`callback`은 브라우저 이동이라 GET이며, callback은 세션에 묶인 "
            "난수 state를 검증하고 한 번 쓴 뒤 폐기합니다. "
            "`messages`는 **연결 확인용 실시간 조회**라 DB에 저장하지 않습니다. "
            "프로젝트에 원문을 쌓는 것은 ③의 sync입니다."
        ),
    },
    {
        "name": "② 연동 · Slack",
        "description": (
            "워크스페이스·채널·메시지·스레드 조회. Gmail과 마찬가지로 연결 확인용이며 "
            "저장하지 않습니다. 파일은 provider의 `url_private` 대신 `fileId`만 내려가고, "
            "`GET /api/slack/file`이 서버에서 대신 받아 프록시합니다. "
            "bot token을 프론트로 내보내지 않기 위해서입니다."
        ),
    },
    {
        "name": "② 연동 · GitHub",
        "description": (
            "저장소 코드를 읽기 위한 PAT 등록. OAuth가 아니라 사용자가 토큰을 직접 "
            "붙여넣고, Gmail·Slack 토큰과 같은 방식으로 암호화해 저장합니다. "
            "사람마다 접근 가능한 저장소가 달라 서버 공용 토큰으로는 안 되기 때문입니다."
        ),
    },
    {
        "name": "③ 프로젝트",
        "description": (
            "계약 하나가 프로젝트 하나입니다. `DRAFT`로 만들고 계약이 체결되면 사람이 "
            "`ACTIVE`로 올립니다(`PATCH .../status`). 계약 범위가 없으면 판정 기준이 "
            "없어 요청이 전부 주황으로 나오므로, 시작 전에 `POST .../contract`로 "
            "최초 계약을 등록하세요. `unansweredRequestCount`는 대응 대기 중인 요청 수입니다."
        ),
    },
    {
        "name": "③ 수집 · 분석 실행",
        "description": (
            "**여기가 AI 파이프라인의 입구입니다.** source-link로 Gmail 상대 한 명 또는 "
            "Slack 채널 하나를 프로젝트에 연결하고, sync를 부르면 원문을 저장한 뒤 "
            "BackgroundTasks로 분석을 시작합니다. sync는 새 메시지 수와 run ID를 "
            "즉시 돌려주므로, 결과는 잠시 뒤 ④에서 다시 조회하세요. "
            "같은 메시지를 여러 번 sync해도 unique key로 한 번만 저장됩니다."
        ),
    },
    {
        "name": "④ 요청 판정",
        "description": (
            "AI가 원문에서 뽑은 클라이언트 요청 카드입니다. 원문 한 건에 요청이 여러 개면 "
            "여러 카드가 생깁니다. `aiDecisionStatus`가 3색 판정이고 `responseStatus`는 "
            "사람이 대응했는지입니다 — **둘은 독립입니다.** "
            "`GET /api/tickets`는 최신 화면 DTO를 한 번에 주고, 처리 방식·확정값·"
            "발송 표시를 MongoDB에 저장합니다. 답변 초안과 mark-sent는 실제 채널 발송이 아닙니다."
        ),
    },
    {
        "name": "④ 요구사항 · 계약",
        "description": (
            "빨강(`EXTRA_REQUEST`) 판정에서 승격된 요구사항의 합의 흐름과 계약 반영입니다. "
            "상태 전이는 규칙이라 코드가 검증하고, AI는 '합의'·'완료'·'거절'을 제안할 수 "
            "없습니다. **계약을 바꾸는 통로는 `/contract/apply` 하나뿐**이며 그 안에 "
            "합의 여부 검사가 있습니다. 같은 요구사항을 다시 반영해도 한 번만 적용됩니다."
        ),
    },
    {
        "name": "④ 붙여넣기 분석",
        "description": (
            "채널 연동 없이 대화를 그대로 붙여넣어 요구사항을 뽑는 경로입니다. "
            "③의 sync 파이프라인과 다른 흐름이며, 결과는 3색 판정이 아니라 "
            "요구사항 9상태입니다. `projectId`를 주면 그 프로젝트에 귀속됩니다."
        ),
    },
]


_TAG_NAMES = {
    "auth": "① 인증",
    "email": "② 연동 · Gmail",
    "slack": "② 연동 · Slack",
    "github": "② 연동 · GitHub",
    "project": "③ 프로젝트",
    "projects": "③ 프로젝트",
    "ingest": "③ 수집 · 분석 실행",
    "request": "④ 요청 판정",
    "agreement": "④ 요구사항 · 계약",
    "requirements": "④ 요구사항 · 계약",
    "contract": "④ 요구사항 · 계약",
    "analyze": "④ 붙여넣기 분석",
}

_PUBLIC_OPERATIONS = {
    ("/api/health", "get"),
    ("/api/auth/signup", "post"),
    ("/api/auth/login", "post"),
    ("/api/auth/logout", "post"),
    ("/api/auth/demo-session", "post"),
}

_OAUTH_CONNECT_OPERATIONS = {
    ("/api/email/connect", "get"),
    ("/api/slack/connect", "get"),
}

_OAUTH_CALLBACK_OPERATIONS = {
    ("/api/email/callback", "get"),
    ("/api/slack/callback", "get"),
}

_SLACK_FILE_OPERATION = ("/api/slack/file", "get")

_ERROR_STATUS_BY_OPERATION: dict[tuple[str, str], tuple[int, ...]] = {
    ("/api/auth/signup", "post"): (400, 409, 422),
    ("/api/auth/login", "post"): (401, 422),
    ("/api/auth/me", "get"): (401,),
    ("/api/analyze", "post"): (400, 401, 422, 500),
    ("/api/contract", "get"): (401, 404),
    ("/api/contract", "post"): (400, 401, 409, 422),
    ("/api/contract/apply", "post"): (400, 401, 404, 409, 422, 500),
    ("/api/email/status", "get"): (401,),
    ("/api/email/connect", "get"): (401, 500),
    ("/api/email/messages", "post"): (400, 401, 422, 502),
    ("/api/requirements", "get"): (401,),
    ("/api/requirements/{requirement_id}/allowed", "get"): (401, 404, 422),
    ("/api/requirements/{requirement_id}/transition", "post"): (400, 401, 404, 422),
    ("/api/slack/connect", "get"): (401, 500),
    ("/api/slack/workspaces", "post"): (401,),
    ("/api/slack/channels", "post"): (401, 404, 422, 502),
    ("/api/slack/join", "post"): (401, 404, 422, 502),
    ("/api/slack/messages", "post"): (401, 404, 422, 502),
    ("/api/slack/thread", "post"): (401, 404, 422, 502),
    ("/api/projects", "get"): (401, 422),
    ("/api/projects", "post"): (401, 422),
    ("/api/projects/{project_id}", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/status", "patch"): (401, 404, 422),
    ("/api/projects/{project_id}/requests", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/messages", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/context", "get"): (401, 404, 422),
    ("/api/requests/{request_id}", "get"): (401, 404, 422),
    ("/api/requests/{request_id}/solution", "post"): (401, 404, 422, 502),
    ("/api/requests/{request_id}/checklist", "post"): (401, 404, 422),
    ("/api/requests/{request_id}/reply-draft", "post"): (401, 404, 422),
    ("/api/tickets", "get"): (401, 422),
    ("/api/tickets/{ticket_id}", "get"): (401, 404, 422),
    ("/api/requests/{request_id}/decision", "post"): (400, 401, 404, 409, 422),
    ("/api/requests/{request_id}/mark-sent", "post"): (401, 404, 409, 422),
    ("/api/requests/{request_id}/response-status", "patch"): (401, 404, 422),
    ("/api/projects/{project_id}/materials", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/materials/{material_id}", "patch"): (401, 404, 422),
    ("/api/projects/{project_id}/source-links", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/source-links", "post"): (400, 401, 404, 409, 422),
    ("/api/projects/{project_id}/source-links/{source_link_id}/sync", "post"): (400, 401, 404, 422, 502),
    ("/api/analysis-runs/{analysis_run_id}", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/requirements", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/contract", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/contract", "post"): (400, 401, 404, 409, 422),
    ("/api/projects/{project_id}/contract/apply", "post"): (400, 401, 404, 409, 422),
    ("/api/projects/{project_id}/requirements/{requirement_id}/transition", "post"): (400, 401, 404, 422),
}

_ERROR_DESCRIPTIONS = {
    400: "요청 내용을 처리할 수 없음",
    401: "로그인 필요",
    404: "대상을 찾을 수 없음",
    409: "기존 데이터와 충돌",
    422: "입력값 형식 오류",
    500: "서버 처리 실패",
    502: "외부 서비스 연동 실패",
}

_SUMMARY_BY_OPERATION = {
    ("/api/health", "get"): "서버 상태 확인",
    ("/api/auth/signup", "post"): "회원가입 및 자동 로그인",
    ("/api/auth/login", "post"): "이메일·비밀번호 로그인",
    ("/api/auth/logout", "post"): "로그아웃",
    ("/api/auth/demo-session", "post"): "개발용 시연 세션 발급",
    ("/api/auth/me", "get"): "현재 로그인 사용자 조회",
    ("/api/analyze", "post"): "대화에서 요구사항 추출",
    ("/api/contract", "get"): "현재 계약 조회",
    ("/api/contract", "post"): "최초 계약 등록",
    ("/api/contract/apply", "post"): "합의된 요구사항을 계약에 반영",
    ("/api/email/status", "get"): "Gmail 연동 상태 조회",
    ("/api/email/connect", "get"): "Gmail OAuth 시작",
    ("/api/email/callback", "get"): "Google OAuth callback",
    ("/api/email/messages", "post"): "Gmail 최근 메시지 조회",
    ("/api/requirements", "get"): "요구사항 목록 조회",
    ("/api/requirements/{requirement_id}/allowed", "get"): "가능한 상태 전이 조회",
    ("/api/requirements/{requirement_id}/transition", "post"): "요구사항 상태 전이",
    ("/api/slack/connect", "get"): "Slack OAuth 시작",
    ("/api/slack/callback", "get"): "Slack OAuth callback",
    ("/api/slack/workspaces", "post"): "연결된 Slack 워크스페이스 조회",
    ("/api/slack/channels", "post"): "Slack 채널 목록 조회",
    ("/api/slack/join", "post"): "Slack 공개 채널 참여",
    ("/api/slack/messages", "post"): "Slack 채널 메시지 조회",
    ("/api/slack/thread", "post"): "Slack 스레드 답글 조회",
    ("/api/slack/file", "get"): "Slack 파일 조회",
    ("/api/projects", "get"): "프로젝트 목록 조회",
    ("/api/projects", "post"): "프로젝트 생성",
    ("/api/projects/{project_id}", "get"): "프로젝트 상세 조회",
    ("/api/projects/{project_id}/status", "patch"): "프로젝트 상태 변경",
    ("/api/projects/{project_id}/requests", "get"): "프로젝트 요청 목록 조회",
    ("/api/projects/{project_id}/messages", "get"): "프로젝트 고객 메시지 조회",
    ("/api/projects/{project_id}/context", "get"): "프로젝트 자료·개발 현황 조회",
    ("/api/requests/{request_id}", "get"): "요청 상세 및 근거 조회",
    ("/api/requests/{request_id}/solution", "post"): "티켓 솔루션 생성",
    ("/api/requests/{request_id}/checklist", "post"): "답변 전 확인 항목 생성",
    ("/api/requests/{request_id}/reply-draft", "post"): "답변 초안 생성",
    ("/api/tickets", "get"): "프로토타입용 티켓 일감 목록 조회",
    ("/api/tickets/{ticket_id}", "get"): "프로토타입용 티켓 상세 조회",
    ("/api/requests/{request_id}/decision", "post"): "메시지 처리 방식·확정값 저장",
    ("/api/requests/{request_id}/mark-sent", "post"): "답변 발송 완료 표시",
    ("/api/requests/{request_id}/response-status", "patch"): "요청 대응 상태 변경",
    ("/api/projects/{project_id}/materials", "get"): "프로젝트 자료 목록 조회",
    ("/api/projects/{project_id}/materials/{material_id}", "patch"): "자료를 티켓에 할당·해제",
    ("/api/projects/{project_id}/source-links", "get"): "프로젝트 채널 연결 조회",
    ("/api/projects/{project_id}/source-links", "post"): "프로젝트 채널 연결 등록",
    ("/api/projects/{project_id}/source-links/{source_link_id}/sync", "post"): "채널 원문 수집·AI 분석 시작",
    ("/api/analysis-runs/{analysis_run_id}", "get"): "AI 분석 상태 조회",
    ("/api/projects/{project_id}/requirements", "get"): "프로젝트 요구사항 조회",
    ("/api/projects/{project_id}/contract", "get"): "프로젝트 계약 조회",
    ("/api/projects/{project_id}/contract", "post"): "프로젝트 최초 계약 등록",
    ("/api/projects/{project_id}/contract/apply", "post"): "프로젝트 계약 반영",
    ("/api/projects/{project_id}/requirements/{requirement_id}/transition", "post"): "프로젝트 요구사항 전이",
}


def _api_error_response(status: int) -> dict[str, Any]:
    example = {
        400: "요청 내용을 확인해 주세요.",
        401: "로그인이 필요합니다.",
        404: "해당 대상을 찾을 수 없습니다.",
        409: "이미 존재하는 데이터입니다.",
        422: "입력값 형식을 확인해 주세요.",
        500: "요청을 처리하지 못했습니다.",
        502: "외부 서비스에서 데이터를 가져오지 못했습니다.",
    }[status]
    return {
        "description": _ERROR_DESCRIPTIONS[status],
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiError"},
                "example": {"ok": False, "error": example},
            }
        },
    }


def _success_response() -> dict[str, Any]:
    return {
        "description": "성공",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiSuccess"},
                "example": {"ok": True, "data": {}},
            }
        },
    }


def _has_documented_json_schema(response: object) -> bool:
    """라우트가 구체 response_model을 선언했으면 그 스키마를 보존한다."""

    if not isinstance(response, dict):
        return False
    content = response.get("content")
    if not isinstance(content, dict):
        return False
    json_content = content.get("application/json")
    if not isinstance(json_content, dict):
        return False
    schema = json_content.get("schema")
    return isinstance(schema, dict) and bool(schema)


def _redirect_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "headers": {
            "Location": {
                "description": "이동할 OAuth provider 또는 프론트엔드 URL",
                "schema": {"type": "string", "format": "uri"},
            }
        },
    }


def _plain_text_response(status: int) -> dict[str, Any]:
    return {
        "description": _ERROR_DESCRIPTIONS[status],
        "content": {
            "text/plain": {
                "schema": {"type": "string"},
                "example": "파일을 가져오지 못했습니다.",
            }
        },
    }


def _slack_file_response() -> dict[str, Any]:
    binary = {"schema": {"type": "string", "format": "binary"}}
    return {
        "description": "이미지는 inline, 나머지 파일은 attachment로 반환",
        "headers": {
            "Content-Disposition": {
                "description": "inline 또는 attachment 파일명",
                "schema": {"type": "string"},
            }
        },
        "content": {
            "application/octet-stream": deepcopy(binary),
            "image/gif": deepcopy(binary),
            "image/jpeg": deepcopy(binary),
            "image/png": deepcopy(binary),
            "image/webp": deepcopy(binary),
        },
    }


def _add_callback_parameters(operation: dict[str, Any]) -> None:
    parameters = operation.setdefault("parameters", [])
    existing = {
        (parameter.get("name"), parameter.get("in"))
        for parameter in parameters
        if isinstance(parameter, dict)
    }
    for name, description in (
        ("state", "서버가 발급한 OAuth state"),
        ("code", "OAuth provider가 발급한 인가 코드"),
        ("error", "사용자 거부 등 provider 오류"),
    ):
        if (name, "query") not in existing:
            parameters.append(
                {
                    "name": name,
                    "in": "query",
                    "required": False,
                    "description": description,
                    "schema": {"type": "string"},
                }
            )


def _patch_schema(schema: dict[str, Any]) -> None:
    components = schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas["ApiSuccess"] = {
        "type": "object",
        "title": "ApiSuccess",
        "required": ["ok", "data"],
        "properties": {
            "ok": {"type": "boolean", "enum": [True], "default": True},
            "data": {"description": "API별 성공 데이터"},
        },
        "example": {"ok": True, "data": {}},
    }
    schemas["ApiError"] = {
        "type": "object",
        "title": "ApiError",
        "required": ["ok", "error"],
        "properties": {
            "ok": {"type": "boolean", "enum": [False], "default": False},
            "error": {"type": "string", "description": "화면에 표시할 한국어 오류"},
        },
        "example": {"ok": False, "error": "로그인이 필요합니다."},
    }
    components.setdefault("securitySchemes", {})["cookieAuth"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": "agreed_session",
        "description": (
            "Agreed HttpOnly 세션 쿠키. Swagger에서는 auth/login 또는 "
            "auth/signup을 먼저 실행하면 브라우저가 자동 저장합니다."
        ),
    }

    paths = schema.get("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"} or not isinstance(
                operation, dict
            ):
                continue

            key = (path, method)
            tags = operation.get("tags") or []
            operation["tags"] = [_TAG_NAMES.get(tag, tag) for tag in tags] or ["system"]
            if key == ("/api/health", "get"):
                operation["tags"] = ["시스템"]
            if key in _SUMMARY_BY_OPERATION:
                operation["summary"] = _SUMMARY_BY_OPERATION[key]

            if key not in _PUBLIC_OPERATIONS:
                operation["security"] = [{"cookieAuth": []}]

            responses = operation.setdefault("responses", {})
            if key in _OAUTH_CONNECT_OPERATIONS:
                responses.pop("200", None)
                responses["307"] = _redirect_response("OAuth provider 동의 화면으로 이동")
                operation["description"] = (
                    "프론트엔드가 fetch하지 말고 `window.location.href`로 이동할 경로입니다."
                )
            elif key in _OAUTH_CALLBACK_OPERATIONS:
                responses.clear()
                responses["307"] = _redirect_response("연동 결과를 포함한 프론트엔드 URL로 이동")
                operation["description"] = (
                    "OAuth provider전용 callback입니다. 프론트엔드에서 직접 호출하지 않습니다."
                )
                _add_callback_parameters(operation)
            elif key == _SLACK_FILE_OPERATION:
                responses["200"] = _slack_file_response()
                for status in (400, 401, 404, 502):
                    responses[str(status)] = _plain_text_response(status)
                # query/path 파싱 실패는 전역 validation handler를 거친다.
                responses["422"] = _api_error_response(422)
            else:
                # Auth처럼 endpoint가 구체 response_model을 가진 경우에는 정확한
                # DTO를 유지하고, 빈 schema만 공통 envelope로 보정한다.
                if "200" in responses and not _has_documented_json_schema(responses["200"]):
                    responses["200"] = _success_response()

            if key != _SLACK_FILE_OPERATION:
                for status in _ERROR_STATUS_BY_OPERATION.get(key, ()):
                    responses[str(status)] = _api_error_response(status)
                # FastAPI가 자동 생성한 HTTPValidationError는 실제 전역 handler와 다르다.
                if "422" in responses:
                    responses["422"] = _api_error_response(422)


def configure_openapi(app: FastAPI) -> None:
    """기본 OpenAPI를 생성한 뒤 실제 응답 규약으로 보정한다."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            webhooks=app.webhooks.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            terms_of_service=app.terms_of_service,
            contact=app.contact,
            license_info=app.license_info,
            separate_input_output_schemas=app.separate_input_output_schemas,
        )
        _patch_schema(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
