"""Agreed 이메일·비밀번호 로그인과 서버 측 세션."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
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
    email: str
    password: str


class SignupRequest(LoginRequest):
    name: str


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


def _public_user(user: User) -> dict[str, str]:
    return {"id": str(user.id), "name": user.name, "email": user.email}


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


@router.post("/signup")
async def signup(body: SignupRequest):
    name = body.name.strip()
    if not 1 <= len(name) <= 50:
        return fail("이름은 1자 이상 50자 이하로 입력해 주세요.")

    email = _normalize_email(body.email)
    if email is None:
        return fail("이메일 형식을 확인해 주세요.")
    if not 8 <= len(body.password) <= 128:
        return fail("비밀번호는 8자 이상 128자 이하로 입력해 주세요.")

    password_hash = await run_in_threadpool(hash_password, body.password)
    user = User(name=name, email=email, passwordHash=password_hash)
    try:
        await user.insert()
    except DuplicateKeyError:
        return fail("이미 가입된 이메일입니다.", 409)
    return await _issue_session(user)


@router.post("/login")
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


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = await Session.find_one(Session.tokenHash == hash_session_token(token))
        if session is not None:
            await session.delete()

    response = ok({"loggedOut": True})
    clear_session_cookie(response)
    return response


@router.get("/me")
async def me(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    return ok({"user": _public_user(current_user)})
