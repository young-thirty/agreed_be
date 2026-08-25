from datetime import date, datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import DevelopmentStatus, ProjectStatus


def utc_now() -> datetime:
    return datetime.utcnow()


class Project(Document):
    ownerId: PydanticObjectId
    name: str = Field(min_length=1, max_length=120)
    clientName: str = Field(min_length=1, max_length=120)
    # 이 주소와 주고받은 메일에서 요구사항을 뽑는다. 계약 시점에 모를 수 있어 선택이다.
    clientEmail: str | None = Field(default=None, max_length=320)
    description: str = Field(default="", max_length=1000)
    startDate: date | None = None
    endDate: date | None = None
    contractPrice: int | None = Field(default=None, ge=0)
    status: ProjectStatus = "DRAFT"
    statusRank: int = 1
    development: DevelopmentStatus | None = None
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "projects"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("statusRank", ASCENDING), ("updatedAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("updatedAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("createdAt", DESCENDING)]),
        ]
