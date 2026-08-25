"""Gmail·Slack 외부 연동 어댑터의 공통 HTTP 경계."""

import httpx

HTTP_TIMEOUT_SECONDS = 8.0


class IntegrationError(RuntimeError):
    """외부 연동 호출이나 응답 형식이 올바르지 않을 때 발생한다."""


def create_http_client() -> httpx.AsyncClient:
    """모든 연동 호출에 동일한 8초 제한을 적용한다."""

    return httpx.AsyncClient(timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS))
