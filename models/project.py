from datetime import date, datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import ProjectStatus


def utc_now() -> datetime:
    return datetime.utcnow()


class Project(Document):
    ownerId: PydanticObjectId
    name: str = Field(min_length=1, max_length=120)
    clientName: str = Field(min_length=1, max_length=120)
    startDate: date | None = None
    endDate: date | None = None
    contractPrice: int | None = Field(default=None, ge=0)
    status: ProjectStatus = "DRAFT"
    statusRank: int = 1
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "projects"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("statusRank", ASCENDING), ("updatedAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("updatedAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("createdAt", DESCENDING)]),
        ]
