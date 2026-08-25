"""pwdlib의 Argon2 기본 설정을 사용한 비밀번호 해시."""

from pwdlib import PasswordHash


_password_hash = PasswordHash.recommended()
_dummy_password_hash = _password_hash.hash("agreed-login-timing-placeholder")


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hash.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


def verify_login_password(password: str, password_hash: str | None) -> bool:
    """계정이 없어도 Argon2 검증을 수행해 로그인 시간 차이를 줄인다."""
    candidate_hash = _dummy_password_hash if password_hash is None else password_hash
    verified = verify_password(password, candidate_hash)
    return password_hash is not None and verified
