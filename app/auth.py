"""HttpOnly opaque 세션 쿠키를 로그인 사용자로 바꾸는 공통 의존성."""

import os
from datetime import datetime, timedelta
from typing import Literal, cast

from fastapi import Request
from fastapi.responses import Response

from infra.security.tokens import hash_session_token
from models.session import Session
from models.user import User


SESSION_COOKIE_NAME = "agreed_session"


def _session_ttl_days() -> int:
    try:
        days = int(os.environ.get("SESSION_TTL_DAYS", "14"))
    except ValueError as error:
        raise RuntimeError("SESSION_TTL_DAYS는 양의 정수여야 합니다.") from error
    if days < 1:
        raise RuntimeError("SESSION_TTL_DAYS는 양의 정수여야 합니다.")
    return days


def _session_cookie_samesite() -> Literal["lax", "strict", "none"]:
    value = os.environ.get("SESSION_COOKIE_SAMESITE", "lax").strip().lower()
    if value not in {"lax", "strict", "none"}:
        raise RuntimeError("SESSION_COOKIE_SAMESITE는 lax, strict, none 중 하나여야 합니다.")
    return cast(Literal["lax", "strict", "none"], value)


def _session_cookie_secure() -> bool:
    value = os.environ.get("SESSION_COOKIE_SECURE", "false").strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError("SESSION_COOKIE_SECURE는 true 또는 false여야 합니다.")
    return value == "true"


def session_lifetime() -> timedelta:
    # app.main이 모듈 import 후 load_dotenv를 실행하므로 요청 처리 시점에 읽는다.
    return timedelta(days=_session_ttl_days())


def _cookie_settings() -> tuple[bool, Literal["lax", "strict", "none"]]:
    secure = _session_cookie_secure()
    samesite = _session_cookie_samesite()
    if samesite == "none" and not secure:
        raise RuntimeError(
            "SameSite=None 세션 쿠키는 SESSION_COOKIE_SECURE=true가 필요합니다."
        )
    return secure, samesite


def utc_now() -> datetime:
    # app.main의 MongoDB 클라이언트가 naive UTC datetime을 사용한다.
    return datetime.utcnow()


async def get_current_user(request: Request) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    session = await Session.find_one(Session.tokenHash == hash_session_token(token))
    if session is None:
        return None

    if session.expiresAt <= utc_now():
        await session.delete()
        return None

    user = await User.get(session.userId)
    if user is None:
        await session.delete()
        return None
    return user


def set_session_cookie(response: Response, token: str) -> None:
    secure, samesite = _cookie_settings()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(session_lifetime().total_seconds()),
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    secure, samesite = _cookie_settings()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )
