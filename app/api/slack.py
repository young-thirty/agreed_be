"""로그인 사용자에게 귀속되는 Slack OAuth와 조회 API."""

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.responses import RedirectResponse, Response

from app.auth import get_current_user
from app.integration_store import (
    access_token,
    save_slack_connection,
    slack_connection,
    slack_connections,
)
from app.oauth import (
    consume_oauth_state,
    frontend_result_url,
    new_oauth_state,
    set_oauth_state_cookie,
)
from app.response import fail, ok
from infra.integrations import IntegrationError
from infra.integrations.slack import (
    SLACK_SCOPES,
    SlackFileTooLargeError,
    build_auth_url,
    exchange_code,
    fetch_file,
    fetch_history,
    fetch_replies,
    join_channel,
    list_channels,
)
from infra.security.provider_tokens import TokenEncryptionError
from models.user import User

router = APIRouter(prefix="/slack", tags=["slack"])
MAX_FILE_BYTES = 10 * 1024 * 1024
SAFE_INLINE_IMAGE_TYPES = {"image/gif", "image/jpeg", "image/png", "image/webp"}


class TeamRequest(BaseModel):
    teamId: str = Field(min_length=1)


class ChannelRequest(TeamRequest):
    channelId: str = Field(min_length=1)


class MessagesRequest(ChannelRequest):
    oldest: str | None = None


class ThreadRequest(MessagesRequest):
    threadTs: str = Field(min_length=1)


def _slack_config() -> tuple[str, str, str]:
    values = tuple(
        os.environ.get(name, "").strip()
        for name in (
            "SLACK_CLIENT_ID",
            "SLACK_CLIENT_SECRET",
            "SLACK_REDIRECT_URI",
        )
    )
    if not all(values):
        raise RuntimeError("Slack OAuth 환경설정이 비어 있습니다.")
    return values


async def _owned_connection(user: User, team_id: str):
    return await slack_connection(str(user.id), team_id)


@router.get("/connect")
async def connect_slack(
    request: Request,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("Slack을 연결하려면 먼저 로그인해 주세요.", 401)

    try:
        client_id, _, redirect_uri = _slack_config()
        state = new_oauth_state("slack", str(current_user.id), request)
        response = RedirectResponse(
            build_auth_url(client_id=client_id, redirect_uri=redirect_uri, state=state)
        )
        set_oauth_state_cookie(response, "slack", state)
        return response
    except (RuntimeError, TokenEncryptionError, ValueError):
        return fail(
            "Slack 연동 설정이 없습니다. 백엔드 환경변수를 확인해 주세요.",
            500,
        )


@router.get("/callback")
async def slack_callback(
    request: Request,
    current_user: User | None = Depends(get_current_user),
):
    result = RedirectResponse(frontend_result_url("slack", "failed"))
    returned_state = request.query_params.get("state")
    state_is_valid = consume_oauth_state(
        request,
        result,
        "slack",
        returned_state,
        str(current_user.id) if current_user is not None else None,
    )
    if current_user is None:
        result.headers["location"] = frontend_result_url("slack", "login_required")
        return result
    if not state_is_valid:
        return result
    if request.query_params.get("error"):
        result.headers["location"] = frontend_result_url("slack", "denied")
        return result

    code = request.query_params.get("code")
    if not code:
        return result

    try:
        client_id, client_secret, redirect_uri = _slack_config()
        installation = await exchange_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
        await save_slack_connection(
            owner_id=str(current_user.id),
            team_id=installation.teamId,
            team_name=installation.teamName,
            bot_token=installation.botToken,
            scopes=list(SLACK_SCOPES),
        )
    except (IntegrationError, RuntimeError, TokenEncryptionError, ValueError):
        return result

    result.headers["location"] = frontend_result_url("slack", "connected")
    return result


@router.post("/workspaces")
async def workspaces(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connections = await slack_connections(str(current_user.id))
    return ok(
        [
            {"teamId": connection.externalId, "teamName": connection.externalName}
            for connection in sorted(connections, key=lambda item: item.externalName.casefold())
        ]
    )


@router.post("/channels")
async def channels(
    body: TeamRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await _owned_connection(current_user, body.teamId)
    if connection is None:
        return fail("연결되지 않은 워크스페이스입니다. Slack을 다시 연결해 주세요.", 404)
    try:
        return ok(await list_channels(bot_token=access_token(connection)))
    except (IntegrationError, TokenEncryptionError):
        return fail("채널 목록을 가져오지 못했습니다. 다시 시도해 주세요.", 502)


@router.post("/join")
async def join(
    body: ChannelRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await _owned_connection(current_user, body.teamId)
    if connection is None:
        return fail("연결되지 않은 워크스페이스입니다. Slack을 다시 연결해 주세요.", 404)
    try:
        await join_channel(
            bot_token=access_token(connection),
            channel_id=body.channelId,
        )
        return ok({"joined": True})
    except (IntegrationError, TokenEncryptionError):
        return fail(
            "이 채널에 봇을 추가하지 못했습니다. 비공개 채널이면 Slack에서 직접 초대해 주세요.",
            502,
        )


@router.post("/messages")
async def messages(
    body: MessagesRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await _owned_connection(current_user, body.teamId)
    if connection is None:
        return fail("연결되지 않은 워크스페이스입니다. Slack을 다시 연결해 주세요.", 404)
    try:
        return ok(
            await fetch_history(
                bot_token=access_token(connection),
                channel_id=body.channelId,
                oldest=body.oldest,
            )
        )
    except (IntegrationError, TokenEncryptionError):
        return fail(
            "이 채널의 메시지를 가져오지 못했습니다. 봇이 채널에 있는지 확인해 주세요.",
            502,
        )


@router.post("/thread")
async def thread(
    body: ThreadRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await _owned_connection(current_user, body.teamId)
    if connection is None:
        return fail("연결되지 않은 워크스페이스입니다. Slack을 다시 연결해 주세요.", 404)
    try:
        return ok(
            await fetch_replies(
                bot_token=access_token(connection),
                channel_id=body.channelId,
                thread_ts=body.threadTs,
                oldest=body.oldest,
            )
        )
    except (IntegrationError, TokenEncryptionError):
        return fail("스레드를 가져오지 못했습니다. 다시 시도해 주세요.", 502)


@router.get("/file")
async def slack_file(
    teamId: str,
    fileId: str,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return Response("로그인이 필요합니다.", status_code=401)
    connection = await _owned_connection(current_user, teamId)
    if connection is None:
        return Response("연결되지 않은 워크스페이스입니다.", status_code=404)

    try:
        downloaded = await fetch_file(
            bot_token=access_token(connection),
            file_id=fileId,
            max_bytes=MAX_FILE_BYTES,
        )
    except SlackFileTooLargeError:
        return Response("10MB가 넘는 파일은 미리 볼 수 없습니다.", status_code=400)
    except (IntegrationError, TokenEncryptionError):
        return Response("파일을 가져오지 못했습니다.", status_code=502)

    if len(downloaded.content) > MAX_FILE_BYTES:
        return Response("10MB가 넘는 파일은 미리 볼 수 없습니다.", status_code=400)

    content_type = downloaded.contentType.split(";", 1)[0].strip().lower()
    inline = content_type in SAFE_INLINE_IMAGE_TYPES
    disposition = "inline" if inline else "attachment"
    encoded_name = quote(downloaded.fileName, safe="")
    return Response(
        content=downloaded.content,
        media_type=content_type if inline else "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{encoded_name}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
