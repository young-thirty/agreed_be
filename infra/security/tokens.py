"""브라우저에 둘 opaque 세션 토큰과 DB 저장용 해시."""

import hashlib
import secrets


def create_session_token() -> str:
    """256bit 난수를 URL-safe 문자열로 만든다."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    """원문 토큰을 저장하지 않고 조회할 고정 길이 SHA-256 해시를 만든다."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
