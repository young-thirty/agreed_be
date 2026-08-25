"""Slack OAuth와 채널·스레드·파일 읽기 어댑터."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from pydantic import BaseModel

from core.channel_data import SlackChannel, SlackFile, SlackMessage
from infra.integrations import IntegrationError, create_http_client

SLACK_API_BASE_URL = "https://slack.com/api"
SLACK_AUTH_URL = "https://slack.com/oauth/v2/authorize"
SLACK_SCOPES = (
    "channels:read",
    "channels:history",
    "channels:join",
    "groups:read",
    "groups:history",
    "users:read",
    "files:read",
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_FILE_REDIRECTS = 3
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024


class SlackFileTooLargeError(IntegrationError):
    """파일이 허용 크기를 넘었을 때 API가 400으로 구분할 수 있게 한다."""


class SlackInstallation(BaseModel):
    teamId: str
    teamName: str
    botToken: str


@dataclass(frozen=True, slots=True)
class DownloadedSlackFile:
    fileId: str
    fileName: str
    contentType: str
    content: bytes


def build_auth_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """로그인 세션에 묶인 난수 state를 포함한 Slack 설치 URL을 만든다."""

    if not state:
        raise ValueError("OAuth state가 비어 있습니다.")
    params = {
        "client_id": client_id,
        "scope": ",".join(SLACK_SCOPES),
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{SLACK_AUTH_URL}?{urlencode(params)}"


def _json_object(response: httpx.Response, service: str) -> dict[str, Any]:
    if not response.is_success:
        raise IntegrationError(f"{service} 요청이 실패했습니다. ({response.status_code})")
    try:
        payload = response.json()
    except ValueError as error:
        raise IntegrationError(
            f"{service}가 올바른 JSON을 반환하지 않았습니다."
        ) from error
    if not isinstance(payload, dict):
        raise IntegrationError(f"{service} 응답 형식이 올바르지 않습니다.")
    return payload


async def _slack_api(
    client: httpx.AsyncClient,
    method: str,
    bot_token: str,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.post(
            f"{SLACK_API_BASE_URL}/{method}",
            headers={"Authorization": f"Bearer {bot_token}"},
            data=params or {},
        )
    except httpx.HTTPError as error:
        raise IntegrationError("Slack에 연결하지 못했습니다.") from error

    payload = _json_object(response, "Slack")
    if payload.get("ok") is not True:
        provider_error = payload.get("error")
        detail = provider_error if isinstance(provider_error, str) else "unknown_error"
        raise IntegrationError(f"Slack {method} 요청이 거절되었습니다: {detail}")
    return payload


async def exchange_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> SlackInstallation:
    async with create_http_client() as client:
        try:
            response = await client.post(
                f"{SLACK_API_BASE_URL}/oauth.v2.access",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
        except httpx.HTTPError as error:
            raise IntegrationError("Slack OAuth 서버에 연결하지 못했습니다.") from error

    payload = _json_object(response, "Slack OAuth")
    if payload.get("ok") is not True:
        raise IntegrationError("Slack 연결 승인을 완료하지 못했습니다.")
    access_token = payload.get("access_token")
    team = payload.get("team")
    if not isinstance(access_token, str) or not access_token or not isinstance(team, dict):
        raise IntegrationError("Slack OAuth 응답 형식이 올바르지 않습니다.")
    team_id = team.get("id")
    team_name = team.get("name")
    if not isinstance(team_id, str) or not isinstance(team_name, str):
        raise IntegrationError("Slack 워크스페이스 정보를 확인하지 못했습니다.")
    return SlackInstallation(teamId=team_id, teamName=team_name, botToken=access_token)


async def list_channels(*, bot_token: str) -> list[SlackChannel]:
    async with create_http_client() as client:
        payload = await _slack_api(
            client,
            "conversations.list",
            bot_token,
            {"types": "public_channel,private_channel", "limit": "200"},
        )

    raw_channels = payload.get("channels")
    if not isinstance(raw_channels, list):
        raise IntegrationError("Slack 채널 목록 형식이 올바르지 않습니다.")
    channels: list[SlackChannel] = []
    for raw in raw_channels:
        if not isinstance(raw, dict):
            continue
        channel_id = raw.get("id")
        name = raw.get("name")
        if not isinstance(channel_id, str) or not isinstance(name, str):
            continue
        channels.append(
            SlackChannel(
                id=channel_id,
                name=name,
                isPrivate=raw.get("is_private") is True,
                isMember=raw.get("is_member") is True,
            )
        )
    return channels


async def join_channel(*, bot_token: str, channel_id: str) -> None:
    async with create_http_client() as client:
        await _slack_api(
            client,
            "conversations.join",
            bot_token,
            {"channel": channel_id},
        )


def _timestamp_to_iso(timestamp: str) -> str:
    try:
        value = float(timestamp)
    except ValueError as error:
        raise IntegrationError("Slack 메시지 시각이 올바르지 않습니다.") from error
    return (
        datetime.fromtimestamp(value, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _to_slack_file(raw: dict[str, Any]) -> SlackFile | None:
    file_id = raw.get("id")
    name = raw.get("name")
    mime_type = raw.get("mimetype")
    if not isinstance(file_id, str) or not isinstance(name, str):
        return None
    return SlackFile(
        fileId=file_id,
        name=name,
        isImage=isinstance(mime_type, str) and mime_type.startswith("image/"),
    )


def _message_user_ids(messages: list[dict[str, Any]]) -> list[str]:
    return list(
        dict.fromkeys(
            user_id
            for message in messages
            if isinstance((user_id := message.get("user")), str)
        )
    )


async def _resolve_user_names(
    client: httpx.AsyncClient,
    bot_token: str,
    user_ids: list[str],
) -> dict[str, str]:
    async def resolve(user_id: str) -> tuple[str, str]:
        try:
            payload = await _slack_api(
                client,
                "users.info",
                bot_token,
                {"user": user_id},
            )
        except IntegrationError:
            return user_id, user_id
        user = payload.get("user")
        if not isinstance(user, dict):
            return user_id, user_id
        real_name = user.get("real_name")
        name = user.get("name")
        if isinstance(real_name, str) and real_name:
            return user_id, real_name
        return user_id, name if isinstance(name, str) and name else user_id

    return dict(await asyncio.gather(*(resolve(user_id) for user_id in user_ids)))


def _to_slack_message(
    raw: dict[str, Any],
    user_names: dict[str, str],
) -> SlackMessage | None:
    timestamp = raw.get("ts")
    if not isinstance(timestamp, str):
        return None
    user_id = raw.get("user")
    if not isinstance(user_id, str):
        user_id = ""
    raw_files = raw.get("files")
    files: list[SlackFile] = []
    if isinstance(raw_files, list):
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            file = _to_slack_file(item)
            if file is not None:
                files.append(file)
    text = raw.get("text")
    reply_count = raw.get("reply_count")
    return SlackMessage(
        id=timestamp,
        userId=user_id,
        userName=user_names.get(user_id, user_id or "알 수 없음"),
        text=text if isinstance(text, str) else "",
        sentAt=_timestamp_to_iso(timestamp),
        replyCount=reply_count if isinstance(reply_count, int) else 0,
        files=files,
    )


def _conversation_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise IntegrationError("Slack 메시지 목록 형식이 올바르지 않습니다.")
    return [
        message
        for message in raw_messages
        if isinstance(message, dict)
        and message.get("type") == "message"
        and message.get("subtype") in {None, "file_share"}
    ]


async def fetch_history(
    *,
    bot_token: str,
    channel_id: str,
    oldest: str | None = None,
) -> list[SlackMessage]:
    params = {"channel": channel_id, "limit": "50"}
    if oldest:
        params["oldest"] = oldest
    async with create_http_client() as client:
        payload = await _slack_api(
            client,
            "conversations.history",
            bot_token,
            params,
        )
        raw_messages = _conversation_messages(payload)
        user_names = await _resolve_user_names(
            client,
            bot_token,
            _message_user_ids(raw_messages),
        )

    messages = [
        message
        for raw in raw_messages
        if (message := _to_slack_message(raw, user_names)) is not None
    ]
    return sorted(messages, key=lambda item: item.sentAt)


async def fetch_replies(
    *,
    bot_token: str,
    channel_id: str,
    thread_ts: str,
    oldest: str | None = None,
) -> list[SlackMessage]:
    params = {"channel": channel_id, "ts": thread_ts, "limit": "100"}
    if oldest:
        params["oldest"] = oldest
    async with create_http_client() as client:
        payload = await _slack_api(
            client,
            "conversations.replies",
            bot_token,
            params,
        )
        raw_messages = [
            message
            for message in _conversation_messages(payload)
            if message.get("ts") != thread_ts
        ]
        user_names = await _resolve_user_names(
            client,
            bot_token,
            _message_user_ids(raw_messages),
        )

    replies = [
        message
        for raw in raw_messages
        if (message := _to_slack_message(raw, user_names)) is not None
    ]
    return sorted(replies, key=lambda item: item.sentAt)


def _validated_slack_file_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise IntegrationError("Slack 파일 주소를 확인하지 못했습니다.")
    parsed = urlparse(value)
    hostname = parsed.hostname or ""
    if parsed.scheme != "https" or not (
        hostname == "slack.com" or hostname.endswith(".slack.com")
    ):
        raise IntegrationError("Slack이 허용되지 않은 파일 주소를 반환했습니다.")
    return value


async def _download_file(
    client: httpx.AsyncClient,
    *,
    url: str,
    bot_token: str,
    max_bytes: int,
) -> tuple[httpx.Headers, bytes]:
    target = _validated_slack_file_url(url)
    for _ in range(_MAX_FILE_REDIRECTS + 1):
        try:
            request = client.build_request(
                "GET",
                target,
                headers={"Authorization": f"Bearer {bot_token}"},
            )
            response = await client.send(request, stream=True, follow_redirects=False)
        except httpx.HTTPError as error:
            raise IntegrationError("Slack 파일을 가져오지 못했습니다.") from error

        try:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise IntegrationError("Slack 파일 이동 주소가 없습니다.")
                target = _validated_slack_file_url(urljoin(target, location))
                continue

            if not response.is_success:
                raise IntegrationError(
                    f"Slack 파일 요청이 실패했습니다. ({response.status_code})"
                )

            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise SlackFileTooLargeError("Slack 파일이 허용 크기를 넘었습니다.")
                except ValueError:
                    pass

            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > max_bytes:
                    raise SlackFileTooLargeError("Slack 파일이 허용 크기를 넘었습니다.")
                content.extend(chunk)
            return response.headers, bytes(content)
        finally:
            await response.aclose()

    raise IntegrationError("Slack 파일 이동 횟수가 너무 많습니다.")


async def fetch_file(
    *,
    bot_token: str,
    file_id: str,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> DownloadedSlackFile:
    """fileId로 files.info를 조회한 뒤, 서버 안에서만 비공개 URL을 사용한다."""

    async with create_http_client() as client:
        payload = await _slack_api(
            client,
            "files.info",
            bot_token,
            {"file": file_id},
        )
        raw_file = payload.get("file")
        if not isinstance(raw_file, dict):
            raise IntegrationError("Slack 파일 정보 형식이 올바르지 않습니다.")
        declared_size = raw_file.get("size")
        if isinstance(declared_size, int) and declared_size > max_bytes:
            raise SlackFileTooLargeError("Slack 파일이 허용 크기를 넘었습니다.")
        private_url = _validated_slack_file_url(
            raw_file.get("url_private_download") or raw_file.get("url_private")
        )
        response_headers, content = await _download_file(
            client,
            url=private_url,
            bot_token=bot_token,
            max_bytes=max_bytes,
        )

    name = raw_file.get("name")
    mime_type = raw_file.get("mimetype")
    return DownloadedSlackFile(
        fileId=file_id,
        fileName=name if isinstance(name, str) and name else file_id,
        contentType=(
            response_headers.get("content-type")
            or (mime_type if isinstance(mime_type, str) else None)
            or "application/octet-stream"
        ),
        content=content,
    )
