"""고객 메시지 하나에 대해 사람이 내린 판단과 발송 표시."""

from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from core.project_data import TicketHandling


def utc_now() -> datetime:
    return datetime.utcnow()


class TicketDecision(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    requestId: PydanticObjectId
    sourceMessageId: PydanticObjectId
    handling: TicketHandling
    targetTicketId: PydanticObjectId | None = None
    # 금액·날짜·자유 입력처럼 화면마다 달라질 수 있는 사람 확정값이다.
    values: dict[str, str] = Field(default_factory=dict)
    replyText: str | None = Field(default=None, max_length=5000)
    drafts: dict[str, str] = Field(default_factory=dict)
    sentAt: datetime | None = None
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "ticket_decisions"
        indexes = [
            IndexModel(
                [("ownerId", ASCENDING), ("requestId", ASCENDING), ("sourceMessageId", ASCENDING)],
                unique=True,
            ),
            IndexModel([("ownerId", ASCENDING), ("targetTicketId", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("sentAt", ASCENDING)]),
        ]
