"""Gmail OAuth와 읽기 전용 REST 어댑터."""

import asyncio
import base64
import binascii
import re
import time
from email.utils import getaddresses
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from core.channel_data import EmailAddress, RawEmail
from infra.integrations import IntegrationError, create_http_client

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)


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
    return RawEmail(
        id=message_id,
        threadId=thread_id,
        sentAt=sent_at,
        from_=from_addresses[0] if from_addresses else EmailAddress(name="", address=""),
        to=_parse_addresses(_header(message, "To")),
        cc=_parse_addresses(_header(message, "Cc")),
        subject=_header(message, "Subject"),
        body=_extract_body(message) or (snippet if isinstance(snippet, str) else ""),
    )


async def fetch_my_address(*, access_token: str) -> str:
    async with create_http_client() as client:
        profile = await _gmail_get(client, "profile", access_token)
    address = profile.get("emailAddress")
    if not isinstance(address, str) or not address:
        raise IntegrationError("Gmail 계정 주소를 확인하지 못했습니다.")
    return address


async def fetch_recent(
    *,
    access_token: str,
    max_messages: int = 20,
) -> list[RawEmail]:
    if not 1 <= max_messages <= 100:
        raise ValueError("max_messages는 1 이상 100 이하여야 합니다.")

    query = urlencode({"maxResults": str(max_messages), "q": "-in:chats -in:spam"})
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
        messages = await asyncio.gather(
            *(
                _gmail_get(client, f"messages/{message_id}?format=full", access_token)
                for message_id in message_ids
            )
        )
    return [_to_raw_email(message) for message in messages]
