"""Gmail OAuth와 읽기 전용 REST 어댑터."""

import asyncio
import base64
import binascii
import re
import time
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from core.channel_data import EmailAddress, EmailAttachment, RawEmail
from infra.integrations import IntegrationError, create_http_client

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)

# Gmail은 사용자당 초당 250 quota unit을 준다. messages.get이 건당 5 unit이라
# 100개를 한꺼번에 던지면 버스트가 500 unit이 되어 429가 돌아온다.
MESSAGE_FETCH_CONCURRENCY = 10


class GmailAuthError(IntegrationError):
    """Gmail이 인증 자체를 거부했다. 재연동이 필요한 경우이며, 일시적 실패와 구분한다."""


class GmailTokens(BaseModel):
    accessToken: str
    refreshToken: str
    expiresAt: int


def build_auth_url(*, client_id: str, redirect_uri: str, state: str) -> str:
    """로그인 세션에 묶인 난수 state를 포함한 Google 동의 화면 URL을 만든다."""

    if not state:
        raise ValueError("OAuth state가 비어 있습니다.")
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


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


def _parse_tokens(
    payload: dict[str, Any],
    previous_refresh_token: str | None = None,
) -> GmailTokens:
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token") or previous_refresh_token
    expires_in = payload.get("expires_in")
    if not isinstance(access_token, str) or not access_token:
        raise IntegrationError("Google 응답에 access token이 없습니다.")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise IntegrationError(
            "Google 응답에 refresh token이 없습니다. Gmail을 다시 연결해 주세요."
        )
    if not isinstance(expires_in, (int, float)):
        raise IntegrationError("Google 응답에 token 만료 시간이 없습니다.")
    return GmailTokens(
        accessToken=access_token,
        refreshToken=refresh_token,
        expiresAt=int(time.time() * 1000 + expires_in * 1000),
    )


async def _request_tokens(
    *,
    client_id: str,
    client_secret: str,
    form: dict[str, str],
    previous_refresh_token: str | None = None,
) -> GmailTokens:
    async with create_http_client() as client:
        try:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    **form,
                },
            )
        except httpx.HTTPError as error:
            raise IntegrationError(
                "Google token 서버에 연결하지 못했습니다."
            ) from error
    return _parse_tokens(
        _json_object(response, "Google token"),
        previous_refresh_token,
    )


async def exchange_code(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> GmailTokens:
    return await _request_tokens(
        client_id=client_id,
        client_secret=client_secret,
        form={
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )


async def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> GmailTokens:
    return await _request_tokens(
        client_id=client_id,
        client_secret=client_secret,
        form={
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        previous_refresh_token=refresh_token,
    )


async def _gmail_get(
    client: httpx.AsyncClient,
    path: str,
    access_token: str,
) -> dict[str, Any]:
    try:
        response = await client.get(
            f"{GMAIL_API_BASE_URL}/{path}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError as error:
        raise IntegrationError("Gmail에 연결하지 못했습니다.") from error
    if response.status_code == 401:
        raise GmailAuthError("Gmail이 인증을 거부했습니다.")
    return _json_object(response, "Gmail")


def _header(message: dict[str, Any], name: str) -> str:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return ""
    for item in headers:
        if not isinstance(item, dict):
            continue
        header_name = item.get("name")
        value = item.get("value")
        if isinstance(header_name, str) and header_name.lower() == name.lower():
            return value if isinstance(value, str) else ""
    return ""


def _parse_addresses(value: str) -> list[EmailAddress]:
    return [
        EmailAddress(name=name.strip(), address=address.strip())
        for name, address in getaddresses([value])
        if address.strip()
    ]


def _decode_base64url(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except (ValueError, binascii.Error):
        return ""


def _decode_base64url_bytes(data: str) -> bytes:
    """첨부는 텍스트가 아닐 수 있어 문자열로 디코드하지 않는다."""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (ValueError, binascii.Error) as error:
        raise IntegrationError("Gmail 첨부 데이터를 읽지 못했습니다.") from error


def _find_attachments(part: dict[str, Any]) -> list[EmailAttachment]:
    """MIME 트리에서 첨부 파트만 뽑는다.

    본문 파트는 filename이 없고 body.attachmentId도 없다. 첨부는 파일 이름과
    attachmentId가 함께 있는 파트다 — mimeType은 이미지든 문서든 상관없다.
    """
    found: list[EmailAttachment] = []
    filename = part.get("filename")
    body = part.get("body")
    part_id = part.get("partId")
    if (
        isinstance(filename, str) and filename
        and isinstance(body, dict) and isinstance(part_id, str)
    ):
        attachment_id = body.get("attachmentId")
        if isinstance(attachment_id, str):
            size = body.get("size")
            found.append(
                EmailAttachment(
                    id=part_id,
                    attachmentId=attachment_id,
                    filename=filename,
                    mimeType=str(part.get("mimeType") or "application/octet-stream"),
                    sizeBytes=size if isinstance(size, int) else 0,
                )
            )

    children = part.get("parts")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                found.extend(_find_attachments(child))
    return found


def _find_part(part: dict[str, Any], mime_type: str) -> str:
    if part.get("mimeType") == mime_type:
        body = part.get("body")
        if isinstance(body, dict) and isinstance(body.get("data"), str):
            return _decode_base64url(body["data"])

    children = part.get("parts")
    if isinstance(children, list):
        for child in children:
            if not isinstance(child, dict):
                continue
            found = _find_part(child, mime_type)
            if found:
                return found
    return ""


def _extract_body(message: dict[str, Any]) -> str:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain
    html = _find_part(payload, "text/html")
    if not html:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", without_tags).strip()


def _to_raw_email(message: dict[str, Any]) -> RawEmail:
    message_id = message.get("id")
    thread_id = message.get("threadId")
    internal_date = message.get("internalDate")
    if not isinstance(message_id, str) or not isinstance(thread_id, str):
        raise IntegrationError("Gmail 메시지 식별자가 없습니다.")
    try:
        sent_at = time.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z",
            time.gmtime(int(str(internal_date)) / 1000),
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise IntegrationError("Gmail 메시지 시각이 올바르지 않습니다.") from error

    from_addresses = _parse_addresses(_header(message, "From"))
    snippet = message.get("snippet")
    payload = message.get("payload")
    return RawEmail(
        id=message_id,
        threadId=thread_id,
        sentAt=sent_at,
        from_=from_addresses[0] if from_addresses else EmailAddress(name="", address=""),
        to=_parse_addresses(_header(message, "To")),
        cc=_parse_addresses(_header(message, "Cc")),
        subject=_header(message, "Subject"),
        body=_extract_body(message) or (snippet if isinstance(snippet, str) else ""),
        attachments=_find_attachments(payload) if isinstance(payload, dict) else [],
    )


async def fetch_my_address(*, access_token: str) -> str:
    async with create_http_client() as client:
        profile = await _gmail_get(client, "profile", access_token)
    address = profile.get("emailAddress")
    if not isinstance(address, str) or not address:
        raise IntegrationError("Gmail 계정 주소를 확인하지 못했습니다.")
    return address


def _recent_query(max_messages: int, counterparty: str | None) -> str:
    terms = ["-in:chats", "-in:spam"]
    if counterparty:
        # 상대 주소로 Gmail에서 걸러야 100통을 받아다 한 명 것만 쓰는 낭비가 없어진다.
        terms.append(f"(from:{counterparty} OR to:{counterparty})")
    return urlencode({"maxResults": str(max_messages), "q": " ".join(terms)})


async def fetch_recent(
    *,
    access_token: str,
    max_messages: int = 20,
    counterparty: str | None = None,
) -> list[RawEmail]:
    if not 1 <= max_messages <= 100:
        raise ValueError("max_messages는 1 이상 100 이하여야 합니다.")

    limit = asyncio.Semaphore(MESSAGE_FETCH_CONCURRENCY)

    async def fetch_one(client: httpx.AsyncClient, message_id: str) -> dict[str, Any]:
        async with limit:
            return await _gmail_get(
                client, f"messages/{message_id}?format=full", access_token
            )

    query = _recent_query(max_messages, counterparty)
    async with create_http_client() as client:
        listing = await _gmail_get(client, f"messages?{query}", access_token)
        raw_items = listing.get("messages")
        if raw_items is None:
            return []
        if not isinstance(raw_items, list):
            raise IntegrationError("Gmail 메시지 목록 형식이 올바르지 않습니다.")

        message_ids = [
            item["id"]
            for item in raw_items
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        if not message_ids:
            return []
        results = await asyncio.gather(
            *(fetch_one(client, message_id) for message_id in message_ids),
            return_exceptions=True,
        )

    # 몇 통이 실패해도 나머지는 살린다. 한 통 때문에 전부 빈손으로 돌아오면
    # 사용자에게는 연동이 끊긴 것처럼 보인다.
    for result in results:
        if isinstance(result, GmailAuthError):
            raise result
    messages = [result for result in results if isinstance(result, dict)]
    if not messages:
        raise IntegrationError("Gmail에서 메일 본문을 가져오지 못했습니다.")
    return [_to_raw_email(message) for message in messages]


@dataclass(frozen=True, slots=True)
class DownloadedGmailAttachment:
    attachmentId: str
    fileName: str
    contentType: str
    content: bytes


async def fetch_attachment(
    *,
    access_token: str,
    message_id: str,
    attachment_id: str,
    file_name: str,
    mime_type: str,
) -> DownloadedGmailAttachment:
    """첨부 하나의 실제 바이트를 내려받는다. 목록 조회 때는 부르지 않는다.

    messages.attachments.get은 메시지 안의 파일 이름·MIME 타입을 돌려주지
    않는다. 목록 단계(_find_attachments)에서 이미 알고 있으므로 그대로 받는다.
    """
    async with create_http_client() as client:
        payload = await _gmail_get(
            client, f"messages/{message_id}/attachments/{attachment_id}", access_token
        )
    data = payload.get("data")
    if not isinstance(data, str):
        raise IntegrationError("Gmail 첨부 응답 형식이 올바르지 않습니다.")
    return DownloadedGmailAttachment(
        attachmentId=attachment_id,
        fileName=file_name,
        contentType=mime_type,
        content=_decode_base64url_bytes(data),
    )


def _find_attachment_id_by_part(part: dict[str, Any], part_id: str) -> str | None:
    if part.get("partId") == part_id:
        body = part.get("body")
        attachment_id = body.get("attachmentId") if isinstance(body, dict) else None
        return attachment_id if isinstance(attachment_id, str) else None
    for child in part.get("parts") or []:
        if isinstance(child, dict):
            found = _find_attachment_id_by_part(child, part_id)
            if found is not None:
                return found
    return None


async def fetch_message_attachment(
    *,
    access_token: str,
    message_id: str,
    part_id: str,
    file_name: str,
    mime_type: str,
) -> DownloadedGmailAttachment:
    """목록 조회와 시점이 떨어진 다운로드는 이 함수를 쓴다.

    Gmail의 attachmentId는 messages.get을 부를 때마다 새로 발급되는
    일회성 토큰이다. 목록에서 받아 둔 값을 몇 분 뒤에 다시 쓰면 이미
    유효하지 않다. 그래서 메시지를 다시 조회해 이번 토큰을 새로 받은 뒤에만
    쓴다. part_id(메시지 안에서 이 파트의 위치)는 메시지가 안 바뀌는 한
    그대로라서, 이걸로 같은 첨부를 다시 찾는다.
    """
    async with create_http_client() as client:
        message = await _gmail_get(client, f"messages/{message_id}?format=full", access_token)
    payload = message.get("payload")
    attachment_id = _find_attachment_id_by_part(payload, part_id) if isinstance(payload, dict) else None
    if attachment_id is None:
        raise IntegrationError("Gmail에서 이 첨부를 다시 찾지 못했습니다.")
    return await fetch_attachment(
        access_token=access_token, message_id=message_id, attachment_id=attachment_id,
        file_name=file_name, mime_type=mime_type,
    )
