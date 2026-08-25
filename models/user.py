from datetime import datetime
from typing import Annotated

from beanie import Document, Indexed
from pydantic import Field


def _utc_now() -> datetime:
    # 현재 MongoDB 클라이언트가 naive UTC datetime을 돌려주므로 같은 형식으로 저장한다.
    return datetime.utcnow()


class User(Document):
    """Agreed 자체 로그인 사용자. 비밀번호 원문은 저장하지 않는다."""

    name: str
    email: Annotated[str, Indexed(unique=True)]
    passwordHash: str
    createdAt: datetime = Field(default_factory=_utc_now)

    class Settings:
        name = "users"
