"""Google·Slack 사용자 토큰의 저장 시 암호화.

브라우저와 API 응답에는 provider token을 절대 싣지 않는다. MongoDB에는 이
모듈이 만든 Fernet ciphertext만 저장하고, 실제 외부 API 호출 직전에만 푼다.
"""

import os

from cryptography.fernet import Fernet, InvalidToken


class TokenEncryptionError(RuntimeError):
    """암호화 키가 없거나 ciphertext를 복호화할 수 없을 때 사용한다."""


def _fernet() -> Fernet:
    key = os.environ.get("INTEGRATION_TOKEN_KEY", "").strip()
    if not key:
        raise TokenEncryptionError(
            "INTEGRATION_TOKEN_KEY가 비어 있습니다. 로컬 환경 설정을 먼저 완료해 주세요."
        )

    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise TokenEncryptionError(
            "INTEGRATION_TOKEN_KEY 형식이 올바르지 않습니다. Fernet 키를 다시 생성해 주세요."
        ) from error


def encrypt_secret(value: str) -> str:
    if not value:
        raise TokenEncryptionError("빈 비밀값은 암호화할 수 없습니다.")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str, *, ttl_seconds: int | None = None) -> str:
    try:
        return _fernet().decrypt(
            ciphertext.encode("ascii"),
            ttl=ttl_seconds,
        ).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as error:
        raise TokenEncryptionError(
            "암호화된 비밀값을 읽지 못했습니다. 설정이나 만료 시간을 확인해 주세요."
        ) from error


def encrypt_provider_token(token: str) -> str:
    if not token:
        raise TokenEncryptionError("빈 provider token은 저장할 수 없습니다.")
    return encrypt_secret(token)


def decrypt_provider_token(ciphertext: str) -> str:
    try:
        return decrypt_secret(ciphertext)
    except TokenEncryptionError as error:
        raise TokenEncryptionError(
            "저장된 연동 정보를 읽지 못했습니다. 외부 채널을 다시 연결해 주세요."
        ) from error
