"""로그인 사용자에게 귀속되는 Gmail OAuth와 읽기 API."""

import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import RedirectResponse

from app.auth import get_current_user
from app.integration_store import (
    access_token,
    latest_gmail_connection,
    refresh_token,
    save_gmail_connection,
    utc_now,
)
from app.oauth import (
    consume_oauth_state,
    frontend_result_url,
    new_oauth_state,
    set_oauth_state_cookie,
)
from app.response import fail, ok
from core.channel_data import group_gmail_by_company
from infra.integrations import IntegrationError
from infra.integrations.gmail import (
    GMAIL_SCOPES,
    build_auth_url,
    exchange_code,
    fetch_my_address,
    fetch_recent,
    refresh_access_token,
)
from infra.security.provider_tokens import TokenEncryptionError
from models.user import User

router = APIRouter(prefix="/email", tags=["email"])


class EmailMessagesRequest(BaseModel):
    maxMessages: int = Field(default=20, ge=1, le=100)


def _google_config() -> tuple[str, str, str]:
    values = tuple(
        os.environ.get(name, "").strip()
        for name in (
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "GOOGLE_REDIRECT_URI",
        )
    )
    if not all(values):
        raise RuntimeError("Google OAuth 환경설정이 비어 있습니다.")
    return values


def _expires_at(epoch_milliseconds: int) -> datetime:
    return datetime.utcfromtimestamp(epoch_milliseconds / 1000)


@router.get("/status")
async def gmail_status(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await latest_gmail_connection(str(current_user.id))
    return ok(
        {
            "connected": connection is not None,
            "email": connection.externalId if connection else None,
        }
    )


@router.get("/connect")
async def connect_gmail(
    request: Request,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("Gmail을 연결하려면 먼저 로그인해 주세요.", 401)

    try:
        client_id, _, redirect_uri = _google_config()
        state = new_oauth_state("gmail", str(current_user.id), request)
        response = RedirectResponse(
            build_auth_url(client_id=client_id, redirect_uri=redirect_uri, state=state)
        )
        set_oauth_state_cookie(response, "gmail", state)
        return response
    except (RuntimeError, TokenEncryptionError, ValueError):
        return fail(
            "Google 연동 설정이 없습니다. 백엔드 환경변수를 확인해 주세요.",
            500,
        )


@router.get("/callback")
async def gmail_callback(
    request: Request,
    current_user: User | None = Depends(get_current_user),
):
    result = RedirectResponse(frontend_result_url("gmail", "failed"))
    returned_state = request.query_params.get("state")
    state_is_valid = consume_oauth_state(
        request,
        result,
        "gmail",
        returned_state,
        str(current_user.id) if current_user is not None else None,
    )
    if current_user is None:
        result.headers["location"] = frontend_result_url("gmail", "login_required")
        return result
    if not state_is_valid:
        return result
    if request.query_params.get("error"):
        result.headers["location"] = frontend_result_url("gmail", "denied")
        return result

    code = request.query_params.get("code")
    if not code:
        return result

    try:
        client_id, client_secret, redirect_uri = _google_config()
        tokens = await exchange_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
        # profile 조회는 연결된 외부 계정을 사용자 소유 connection에 묶기 위해 필요하다.
        email = await fetch_my_address(access_token=tokens.accessToken)
        await save_gmail_connection(
            owner_id=str(current_user.id),
            email=email,
            access_token=tokens.accessToken,
            refresh_token=tokens.refreshToken,
            expires_at=_expires_at(tokens.expiresAt),
            scopes=list(GMAIL_SCOPES),
        )
    except (IntegrationError, RuntimeError, TokenEncryptionError, ValueError):
        return result

    result.headers["location"] = frontend_result_url("gmail", "connected")
    return result


@router.post("/messages")
async def gmail_messages(
    body: EmailMessagesRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    owner_id = str(current_user.id)
    connection = await latest_gmail_connection(owner_id)
    if connection is None:
        return fail("Gmail이 연결되어 있지 않습니다. 먼저 Gmail을 연결해 주세요.")

    try:
        token = access_token(connection)
        expires_at = connection.accessTokenExpiresAt
        if expires_at is None or expires_at <= utc_now() + timedelta(minutes=1):
            stored_refresh_token = refresh_token(connection)
            if stored_refresh_token is None:
                return fail("Gmail 연결이 만료되었습니다. Gmail을 다시 연결해 주세요.")
            client_id, client_secret, _ = _google_config()
            refreshed = await refresh_access_token(
                refresh_token=stored_refresh_token,
                client_id=client_id,
                client_secret=client_secret,
            )
            connection = await save_gmail_connection(
                owner_id=owner_id,
                email=connection.externalId,
                access_token=refreshed.accessToken,
                refresh_token=refreshed.refreshToken,
                expires_at=_expires_at(refreshed.expiresAt),
                scopes=connection.scopes,
            )
            token = access_token(connection)

        emails = await fetch_recent(
            access_token=token,
            max_messages=body.maxMessages,
        )
        return ok(group_gmail_by_company(emails, [connection.externalId]))
    except (IntegrationError, RuntimeError, TokenEncryptionError, ValueError):
        return fail(
            "Gmail에서 메일을 가져오지 못했습니다. Gmail을 다시 연결해 주세요.",
            502,
        )
