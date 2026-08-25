from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import Direction, SourceChannel


def utc_now() -> datetime:
    return datetime.utcnow()


class SourceMessage(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    sourceLinkId: PydanticObjectId
    connectionId: str | None = None
    sourceChannel: SourceChannel
    sourceKey: str
    providerMessageId: str
    providerThreadId: str | None = None
    senderExternalId: str | None = None
    senderDisplay: str | None = None
    conversationDisplay: str | None = None
    direction: Direction = "RECEIVED"
    rawText: str = ""
    occurredAt: datetime
    contentHash: str
    attachmentRefs: list[str] = Field(default_factory=list)
    createdAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "source_messages"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("sourceChannel", ASCENDING), ("connectionId", ASCENDING), ("sourceKey", ASCENDING)], unique=True),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("occurredAt", DESCENDING)]),
        ]
