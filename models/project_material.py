from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import (
    Direction, DocumentType, MaterialOrigin, ProcessingStatus, SourceChannel,
)


def utc_now() -> datetime:
    return datetime.utcnow()


class ProjectMaterial(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    # None이면 프로젝트 공용 자료, 값이 있으면 해당 티켓 전용 자료다.
    ticketId: PydanticObjectId | None = None
    origin: MaterialOrigin = "CHANNEL"
    # 이 값이 있어야 아카이브 화면이 채널 아이콘을 join 없이 바로 그린다.
    # 기존 자료(이 필드가 생기기 전에 만든 것)는 None일 수 있다.
    sourceChannel: SourceChannel | None = None
    sourceMessageId: PydanticObjectId | None = None
    # 어느 대화에서 온 파일인지. SourceMessage가 없어도(대화 흐름을 거치지 않고
    # 발견됐어도) 화면에 "누가·무슨 제목으로 보냈는지"를 보여줄 수 있어야 해서
    # 만들 때 값을 그대로 복사해 둔다.
    conversationTitle: str | None = None
    senderDisplay: str | None = None
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
