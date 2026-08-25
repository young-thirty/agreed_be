from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import Direction, DocumentType, MaterialOrigin, ProcessingStatus


def utc_now() -> datetime:
    return datetime.utcnow()


class ProjectMaterial(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    # None이면 프로젝트 공용 자료, 값이 있으면 해당 티켓 전용 자료다.
    ticketId: PydanticObjectId | None = None
    origin: MaterialOrigin = "CHANNEL"
    sourceMessageId: PydanticObjectId | None = None
    connectionId: str | None = None
    providerFileId: str | None = None
    fileName: str = Field(min_length=1, max_length=255)
    mimeType: str | None = None
    sizeBytes: int | None = None
    storageKey: str | None = None
    extractedText: str | None = None
    direction: Direction = "RECEIVED"
    communicatedAt: datetime
    classificationStatus: ProcessingStatus = "PENDING"
    documentType: DocumentType | None = None
    summary: str | None = Field(default=None, max_length=300)
    contentHash: str | None = None
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "project_materials"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("communicatedAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("ticketId", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("sourceMessageId", ASCENDING)]),
            IndexModel(
                [("ownerId", ASCENDING), ("connectionId", ASCENDING), ("providerFileId", ASCENDING)],
                unique=True,
                partialFilterExpression={"providerFileId": {"$type": "string"}},
            ),
        ]
