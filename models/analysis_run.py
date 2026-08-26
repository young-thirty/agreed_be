from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from core.project_data import AnalysisTargetType, ProcessingStatus


def utc_now() -> datetime:
    return datetime.utcnow()


class AnalysisRun(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    targetType: AnalysisTargetType
    sourceMessageId: PydanticObjectId | None = None
    materialId: PydanticObjectId | None = None
    status: ProcessingStatus = "PENDING"
    inputHash: str
    promptVersion: str = "v1"
    model: str = "deepseek-v4-flash"
    errorCode: str | None = None
    startedAt: datetime | None = None
    completedAt: datetime | None = None
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "analysis_runs"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("status", ASCENDING), ("createdAt", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("targetType", ASCENDING), ("inputHash", ASCENDING), ("promptVersion", ASCENDING)], unique=True),
        ]
