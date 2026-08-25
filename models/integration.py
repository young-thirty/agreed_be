from datetime import datetime
from typing import Literal

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


Provider = Literal["gmail", "slack"]


def utc_now() -> datetime:
    # 현재 MongoDB 클라이언트가 naive UTC datetime을 반환하므로 같은 형식으로 저장한다.
    return datetime.utcnow()


class IntegrationConnection(Document):
    """로그인 사용자에게 귀속된 외부 채널 권한.

    accessTokenEncrypted와 refreshTokenEncrypted는 Fernet ciphertext다. 이 문서를
    API 응답으로 직접 반환하지 않고 공개 필드만 별도 dict로 만든다.
    """

    ownerId: str
    provider: Provider
    externalId: str
    externalName: str
    accessTokenEncrypted: str
    refreshTokenEncrypted: str | None = None
    accessTokenExpiresAt: datetime | None = None
    scopes: list[str] = Field(default_factory=list)
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "integration_connections"
        indexes = [
            IndexModel(
                [
                    ("ownerId", ASCENDING),
                    ("provider", ASCENDING),
                    ("externalId", ASCENDING),
                ],
                unique=True,
            ),
            IndexModel([("ownerId", ASCENDING), ("provider", ASCENDING)]),
        ]
