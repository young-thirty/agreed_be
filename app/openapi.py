"""Swagger/OpenAPI 문서 보정.

실행 로직을 바꾸지 않고, JSONResponse를 직접 반환해 FastAPI가
추론하지 못하는 공통 응답·쿠키 인증·redirect·파일 스키마만 문서화한다.
"""

from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


API_DESCRIPTION = """
Agreed 프론트엔드가 호출하는 FastAPI입니다.

### 로컬 Swagger 테스트

1. `POST /api/auth/signup` 또는 `POST /api/auth/login`을 먼저 실행합니다.
2. 브라우저가 HttpOnly `agreed_session` 쿠키를 자동으로 저장합니다.
3. 이후 잠금 표시된 API를 그대로 실행하면 됩니다. 프론트 fetch는
   `credentials: "include"`를 사용합니다.

### 시연용 테스트 계정

`POST /api/auth/login`에서 테스트 계정 이메일 `demo@agreed.local`을 사용합니다.
비밀번호는 팀 내부 공유값으로 입력하고, 로그인 성공 후 같은 브라우저에서 Vercel
프론트로 돌아가 Gmail·Slack 연결을 실행합니다. 브라우저의 서드파티 쿠키 차단이
켜져 있으면 세션 쿠키를 허용해야 합니다.

성공 JSON은 `{ "ok": true, "data": ... }`, 오류 JSON은
`{ "ok": false, "error": "..." }` 형식입니다. OAuth callback은 provider가
호출하는 경로이고, Slack 파일은 JSON이 아닌 binary 응답입니다.

### 현재 구현

- 이메일·비밀번호 인증과 HttpOnly 세션
- Gmail·Slack OAuth 연동과 메시지 조회
- 대화 붙여넣기 분석, 요구사항 전이, 계약 반영
- 프로젝트·원문·요청·자료 저장과 프로젝트별 계약/요구사항 API
- Gmail 상대·Slack 채널 sync 및 BackgroundTasks 기반 요청 분석·자료 분류

### 아직 보류한 범위

실시간 provider 이벤트, 큐/워커, S3/OCR, 페이지네이션, 답장 발송은 시연 이후 단계다.
""".strip()


OPENAPI_TAGS = [
    {
        "name": "시스템",
        "description": "서버 기동 상태 확인.",
    },
    {
        "name": "현재 · 인증",
        "description": "Agreed 회원가입·로그인·로그아웃과 HttpOnly 세션.",
    },
    {
        "name": "현재 · Gmail",
        "description": "로그인 사용자의 Gmail 읽기 전용 연동.",
    },
    {
        "name": "현재 · Slack",
        "description": "로그인 사용자의 Slack 워크스페이스·채널·메시지 조회.",
    },
    {
        "name": "현재 · AI 분석",
        "description": "대화 붙여넣기 분석 시연 API.",
    },
    {
        "name": "현재 · 요구사항",
        "description": "AI가 추출한 요구사항 조회와 사람의 상태 전이.",
    },
    {
        "name": "현재 · 계약",
        "description": "현재 계약 조회·최초 등록·합의된 변경분 반영.",
    },
    {
        "name": "현재 · 프로젝트",
        "description": "프로젝트 목록·상세·소유권 필터와 프로젝트별 계약/요구사항.",
    },
    {
        "name": "현재 · 요청·자료",
        "description": "채널 원문 sync, AI 요청 판정, 자료 분류 결과.",
    },
]


_TAG_NAMES = {
    "auth": "현재 · 인증",
    "email": "현재 · Gmail",
    "slack": "현재 · Slack",
    "analyze": "현재 · AI 분석",
    "requirements": "현재 · 요구사항",
    "contract": "현재 · 계약",
    "projects": "현재 · 프로젝트",
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
    ("/api/requests/{request_id}", "get"): (401, 404, 422),
    ("/api/projects/{project_id}/materials", "get"): (401, 404, 422),
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
    ("/api/requests/{request_id}", "get"): "요청 상세 및 근거 조회",
    ("/api/projects/{project_id}/materials", "get"): "프로젝트 자료 목록 조회",
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
