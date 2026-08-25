"""OAuth redirect에 공통으로 쓰는 짧은 수명의 state 쿠키."""

import json
import os
import secrets
from typing import Literal
from urllib.parse import urlencode

from fastapi import Request
from starlette.responses import Response

from app.auth import SESSION_COOKIE_NAME
from infra.security.provider_tokens import (
    TokenEncryptionError,
    decrypt_secret,
    encrypt_secret,
)
from infra.security.tokens import hash_session_token

Provider = Literal["gmail", "slack"]
STATE_MAX_AGE_SECONDS = 600


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def state_cookie_name(provider: Provider) -> str:
    return f"agreed_oauth_{provider}_state"


def _session_binding(request: Request) -> str | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    return hash_session_token(token) if token else None


def new_oauth_state(provider: Provider, owner_id: str, request: Request) -> str:
    """짧은 수명의 state를 provider와 로그인 사용자에 암호학적으로 묶는다."""

    session_binding = _session_binding(request)
    if session_binding is None:
        raise ValueError("로그인 세션이 없습니다.")
    payload = json.dumps(
        {
            "provider": provider,
            "ownerId": owner_id,
            "sessionHash": session_binding,
            "nonce": secrets.token_urlsafe(32),
        },
        separators=(",", ":"),
    )
    return encrypt_secret(payload)


def frontend_result_url(provider: Provider, result: str) -> str:
    frontend = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000").rstrip("/")
    return f"{frontend}/?{urlencode({provider: result})}"


def set_oauth_state_cookie(response: Response, provider: Provider, state: str) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.set_cookie(
        key=state_cookie_name(provider),
        value=state,
        max_age=STATE_MAX_AGE_SECONDS,
        httponly=True,
        secure=_bool_env("SESSION_COOKIE_SECURE", False),
        # OAuth callback은 외부 사이트의 top-level GET이므로 세션 설정과 분리한다.
        samesite="lax",
        path="/api",
    )


def consume_oauth_state(
    request: Request,
    response: Response,
    provider: Provider,
    returned_state: str | None,
    owner_id: str | None,
) -> bool:
    cookie_name = state_cookie_name(provider)
    response.headers["Cache-Control"] = "private, no-store"
    stored_state = request.cookies.get(cookie_name)
    response.delete_cookie(
        cookie_name,
        path="/api",
        secure=_bool_env("SESSION_COOKIE_SECURE", False),
        httponly=True,
        samesite="lax",
    )
    if not (
        owner_id
        and returned_state
        and stored_state
        and secrets.compare_digest(returned_state, stored_state)
    ):
        return False

    try:
        payload = json.loads(
            decrypt_secret(returned_state, ttl_seconds=STATE_MAX_AGE_SECONDS)
        )
    except (TokenEncryptionError, ValueError, TypeError):
        return False

    return bool(
        isinstance(payload, dict)
        and payload.get("provider") == provider
        and payload.get("ownerId") == owner_id
        and payload.get("sessionHash") == _session_binding(request)
        and isinstance(payload.get("nonce"), str)
    )
