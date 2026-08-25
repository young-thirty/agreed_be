"""로그인 사용자에게 귀속되는 Gmail OAuth와 읽기 API."""

import os
from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import RedirectResponse, Response

from app.api.slack import SAFE_INLINE_IMAGE_TYPES
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
    GmailAuthError,
    build_auth_url,
    exchange_code,
    fetch_message_attachment,
    fetch_my_address,
    fetch_recent,
    refresh_access_token,
)
from infra.security.provider_tokens import TokenEncryptionError
from models.integration import IntegrationConnection
from models.user import User

router = APIRouter(prefix="/email", tags=["email"])

# Gmail API가 첨부 하나에 매기는 상한(25MB)보다 여유 있게 낮춰 둔다.
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


class EmailMessagesRequest(BaseModel):
    maxMessages: int = Field(default=20, ge=1, le=100)
    counterpartyEmail: str | None = Field(
        default=None,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+$",
    )


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


async def _fresh_token(
    owner_id: str,
    connection: IntegrationConnection,
) -> tuple[IntegrationConnection, str]:
    """만료가 임박했으면 갱신한 access token을 돌려준다.

    갱신에 실패하면 재연동 말고는 방법이 없는 상태이므로 GmailAuthError로 올린다.
    호출부가 '다시 연결해 주세요'와 '잠시 후 다시 시도해 주세요'를 구분할 수 있어야 한다.
    """
    expires_at = connection.accessTokenExpiresAt
    if expires_at is not None and expires_at > utc_now() + timedelta(minutes=1):
        return connection, access_token(connection)

    stored_refresh_token = refresh_token(connection)
    if stored_refresh_token is None:
        raise GmailAuthError("Gmail 연결이 만료되었습니다.")

    client_id, client_secret, _ = _google_config()
    try:
        refreshed = await refresh_access_token(
            refresh_token=stored_refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
    except IntegrationError as error:
        raise GmailAuthError("Gmail 연결이 만료되었습니다.") from error

    connection = await save_gmail_connection(
        owner_id=owner_id,
        email=connection.externalId,
        access_token=refreshed.accessToken,
        refresh_token=refreshed.refreshToken,
        expires_at=_expires_at(refreshed.expiresAt),
        scopes=connection.scopes,
    )
    return connection, access_token(connection)


@router.get("/status")
async def gmail_status(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await latest_gmail_connection(str(current_user.id))
    if connection is None:
        return ok({"connected": False, "email": None})

    # 연결 문서가 있다는 것과 지금 메일을 읽을 수 있다는 것은 다르다.
    # 배지는 초록인데 조회는 실패하는 상황을 없애려고 실제로 한 번 물어본다.
    connected = True
    try:
        connection, token = await _fresh_token(str(current_user.id), connection)
        await fetch_my_address(access_token=token)
    except (GmailAuthError, TokenEncryptionError):
        connected = False
    except (IntegrationError, RuntimeError, ValueError):
        # Google이 잠깐 응답하지 않는 경우다. 연결이 끊긴 것으로 보지 않는다.
        pass

    return ok({"connected": connected, "email": connection.externalId})


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
        connection, token = await _fresh_token(owner_id, connection)
        emails = await fetch_recent(
            access_token=token,
            max_messages=body.maxMessages,
            counterparty=body.counterpartyEmail,
        )
    except (GmailAuthError, TokenEncryptionError):
        return fail("Gmail 연결이 끊어졌습니다. Gmail을 다시 연결해 주세요.")
    except (IntegrationError, RuntimeError, ValueError):
        return fail(
            "지금 Gmail에서 메일을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.",
            502,
        )

    return ok(group_gmail_by_company(emails, [connection.externalId]))


@router.get("/attachment")
async def gmail_attachment(
    messageId: str,
    partId: str,
    current_user: User | None = Depends(get_current_user),
):
    """메일 목록에 뜬 첨부 하나를 그 자리에서 읽는다. 저장하지 않는다.

    partId만 받는다. attachmentId는 화면이 메일을 받아 온 시점에만 유효한
    일회성 토큰이라, 화면이 그새 들고 있던 값을 그대로 믿지 않는다 —
    여기서 메시지를 다시 조회해 이번 토큰을 새로 받는다.
    """
    if current_user is None:
        return Response("로그인이 필요합니다.", status_code=401)

    owner_id = str(current_user.id)
    connection = await latest_gmail_connection(owner_id)
    if connection is None:
        return Response("Gmail이 연결되어 있지 않습니다.", status_code=404)

    try:
        connection, token = await _fresh_token(owner_id, connection)
        downloaded = await fetch_message_attachment(
            access_token=token, message_id=messageId, part_id=partId,
        )
    except (GmailAuthError, TokenEncryptionError):
        return Response("Gmail 연결이 끊어졌습니다. Gmail을 다시 연결해 주세요.", status_code=401)
    except IntegrationError:
        return Response("파일을 가져오지 못했습니다.", status_code=502)

    if len(downloaded.content) > MAX_ATTACHMENT_BYTES:
        return Response("10MB가 넘는 파일은 미리 볼 수 없습니다.", status_code=400)

    # Slack 파일 응답(app/api/slack.py)과 같은 정책이다. 이미지 몇 종만 inline을
    # 허락한다. 화면의 PDF·DOCX 뷰어는 fetch로 받아 blob으로 다루므로
    # Content-Disposition·Content-Type이 attachment/octet-stream이어도 상관없다.
    inline = downloaded.contentType in SAFE_INLINE_IMAGE_TYPES
    encoded_name = quote(downloaded.fileName)
    return Response(
        content=downloaded.content,
        media_type=downloaded.contentType if inline else "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{encoded_name}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
