from datetime import datetime
from typing import Annotated

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel


def _utc_now() -> datetime:
    return datetime.utcnow()


class Session(Document):
    """서버 측 세션. 브라우저가 가진 원문 토큰은 DB에 넣지 않는다."""

    tokenHash: Annotated[str, Indexed(unique=True)]
    userId: PydanticObjectId
    expiresAt: datetime
    createdAt: datetime = Field(default_factory=_utc_now)

    class Settings:
        name = "sessions"
        indexes = [
            IndexModel([("userId", ASCENDING)]),
            IndexModel([("expiresAt", ASCENDING)], expireAfterSeconds=0),
        ]
