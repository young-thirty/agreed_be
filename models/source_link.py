from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from core.project_data import SourceChannel


def utc_now() -> datetime:
    return datetime.utcnow()


class ProjectSourceLink(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    connectionId: str | None = None
    sourceChannel: SourceChannel
    displayName: str = Field(min_length=1, max_length=160)
    counterpartyEmail: str | None = None
    threadId: str | None = None
    teamId: str | None = None
    channelId: str | None = None
    locatorKey: str = Field(min_length=1, max_length=300)
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "project_source_links"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING)]),
            IndexModel(
                [("ownerId", ASCENDING), ("projectId", ASCENDING), ("sourceChannel", ASCENDING),
                 ("connectionId", ASCENDING), ("locatorKey", ASCENDING)], unique=True
            ),
        ]
