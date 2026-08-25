"""Agreed 이메일·비밀번호 로그인과 서버 측 세션."""

import os
import secrets
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from app.auth import (
    SESSION_COOKIE_NAME,
    clear_session_cookie,
    get_current_user,
    session_lifetime,
    set_session_cookie,
    utc_now,
)
from app.response import fail, ok
from infra.security.passwords import hash_password, verify_login_password
from infra.security.tokens import create_session_token, hash_session_token
from models.session import Session
from models.user import User


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(
        description="Agreed 가입 이메일",
        examples=["freelancer@example.com"],
    )
    password: str = Field(
        description="비밀번호",
        examples=["demo-password"],
    )


class SignupRequest(LoginRequest):
    name: str = Field(
        description="사용자 이름",
        min_length=1,
        max_length=50,
        examples=["홍길동"],
    )
    phoneNumber: str = Field(
        description="전화번호",
        min_length=1,
        max_length=30,
        examples=["010-1234-5678"],
    )


class DemoSessionRequest(BaseModel):
    """화면 로그인 전 Swagger 시연용 입력. 운영에서는 비활성화한다."""

    email: str = Field(default="demo@agreed.local", examples=["demo@agreed.local"])
    name: str = Field(default="Agreed Demo", min_length=1, max_length=50)
    phoneNumber: str = Field(default="010-0000-0000", min_length=1, max_length=30)


class UserSummary(BaseModel):
    userId: str
    name: str
    email: str
    # 기존 가입자는 값이 없어 null일 수 있고, 신규 가입자는 항상 문자열이다.
    phoneNumber: str | None
    createdAt: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "userId": "66c1234567890abcdef1234",
                    "name": "홍길동",
                    "email": "freelancer@example.com",
                    "phoneNumber": "010-1234-5678",
                    "createdAt": "2026-08-25T12:00:00Z",
                }
            ]
        }
    )


class AuthData(BaseModel):
    user: UserSummary


class AuthSuccessResponse(BaseModel):
    ok: Literal[True]
    data: AuthData


class LogoutData(BaseModel):
    loggedOut: bool


class LogoutSuccessResponse(BaseModel):
    ok: Literal[True]
    data: LogoutData


class ErrorResponse(BaseModel):
    ok: Literal[False]
    error: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"ok": False, "error": "입력값 형식을 확인해 주세요."}]
        }
    )


VALIDATION_RESPONSE = {
    "model": ErrorResponse,
    "description": "입력값 유효성 오류",
}


def _normalize_email(raw_email: str) -> str | None:
    email = raw_email.strip().casefold()
    local, separator, domain = email.partition("@")
    if (
        not separator
        or not local
        or not domain
        or "@" in domain
        or any(character.isspace() for character in email)
        or len(email) > 254
    ):
        return None
    return email


def _public_user(user: User) -> dict[str, object]:
    return {
        "userId": str(user.id),
        "name": user.name,
        "email": user.email,
        "phoneNumber": user.phoneNumber,
        "createdAt": user.createdAt,
    }


async def _issue_session(user: User):
    token = create_session_token()
    session = Session(
        tokenHash=hash_session_token(token),
        userId=user.id,
        expiresAt=utc_now() + session_lifetime(),
    )
    await session.insert()

    response = ok({"user": _public_user(user)})
    set_session_cookie(response, token)
    return response


@router.post(
    "/signup",
    response_model=AuthSuccessResponse,
    summary="회원가입 및 자동 로그인",
    responses={
        400: VALIDATION_RESPONSE,
        409: {
            "model": ErrorResponse,
            "description": "이미 가입된 이메일",
        },
        422: VALIDATION_RESPONSE,
    },
)
async def signup(body: SignupRequest):
    name = body.name.strip()
    if not 1 <= len(name) <= 50:
        return fail("이름은 1자 이상 50자 이하로 입력해 주세요.")

    phone_number = body.phoneNumber.strip()
    if not 1 <= len(phone_number) <= 30:
        return fail("전화번호를 1자 이상 30자 이하로 입력해 주세요.")

    email = _normalize_email(body.email)
    if email is None:
        return fail("이메일 형식을 확인해 주세요.")
    if not 8 <= len(body.password) <= 128:
        return fail("비밀번호는 8자 이상 128자 이하로 입력해 주세요.")

    password_hash = await run_in_threadpool(hash_password, body.password)
    user = User(
        name=name,
        email=email,
        passwordHash=password_hash,
        phoneNumber=phone_number,
    )
    try:
        await user.insert()
    except DuplicateKeyError:
        return fail("이미 가입된 이메일입니다.", 409)
    return await _issue_session(user)


@router.post(
    "/demo-session",
    response_model=AuthSuccessResponse,
    summary="시연용 세션 발급(개발 환경 전용)",
    responses={
        403: {
            "model": ErrorResponse,
            "description": "시연 세션 비활성화",
        },
        422: VALIDATION_RESPONSE,
    },
)
async def demo_session(body: DemoSessionRequest):
    """로그인 화면이 아직 없을 때 Swagger에서 HttpOnly 세션을 만드는 임시 경로."""

    enabled = os.environ.get("DEMO_SESSION_ENABLED", "false").strip().lower() == "true"
    if not enabled:
        return fail("시연 세션은 현재 비활성화되어 있습니다.", 403)
    email = _normalize_email(body.email)
    if email is None:
        return fail("이메일 형식을 확인해 주세요.")
    user = await User.find_one(User.email == email)
    if user is None:
        user = User(
            name=body.name.strip(),
            email=email,
            phoneNumber=body.phoneNumber.strip(),
            # 이 경로는 비밀번호 로그인을 우회하는 시연용 계정 생성이다.
            passwordHash=await run_in_threadpool(hash_password, secrets.token_urlsafe(32)),
        )
        try:
            await user.insert()
        except DuplicateKeyError:
            user = await User.find_one(User.email == email)
            if user is None:
                return fail("시연 계정을 만들지 못했습니다.", 500)
    return await _issue_session(user)


@router.post(
    "/login",
    response_model=AuthSuccessResponse,
    summary="이메일·비밀번호 로그인",
    responses={
        401: {
            "model": ErrorResponse,
            "description": "이메일 또는 비밀번호 불일치",
        },
        422: VALIDATION_RESPONSE,
    },
)
async def login(body: LoginRequest):
    email = _normalize_email(body.email)
    user = None if email is None else await User.find_one(User.email == email)
    password_matches = await run_in_threadpool(
        verify_login_password,
        body.password,
        None if user is None else user.passwordHash,
    )
    if not password_matches:
        return fail("이메일 또는 비밀번호가 맞지 않습니다.", 401)
    assert user is not None
    return await _issue_session(user)


@router.post(
    "/logout",
    response_model=LogoutSuccessResponse,
    summary="로그아웃",
)
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = await Session.find_one(Session.tokenHash == hash_session_token(token))
        if session is not None:
            await session.delete()

    response = ok({"loggedOut": True})
    clear_session_cookie(response)
    return response


@router.get(
    "/me",
    response_model=AuthSuccessResponse,
    summary="현재 로그인 사용자 조회",
    responses={
        401: {
            "model": ErrorResponse,
            "description": "로그인 필요",
        }
    },
)
async def me(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    return ok({"user": _public_user(current_user)})
