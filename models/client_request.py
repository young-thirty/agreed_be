from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import (
    AiDecisionStatus, ClientRequestSummary, DocumentEvidence, ProcessingStatus,
    RequestEvidence, SourceChannel, TicketCategory, TicketSolution, TicketStatus,
)


def utc_now() -> datetime:
    return datetime.utcnow()


class ClientRequest(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    # 티켓을 처음 만든 원문. 상세 화면이 원문을 찾는 기준이다.
    sourceMessageId: PydanticObjectId
    # 이 티켓에 붙은 모든 원문. 후속 인바운드가 같은 티켓으로 매칭되면 늘어난다.
    sourceMessageIds: list[PydanticObjectId] = Field(default_factory=list)
    analysisRunId: PydanticObjectId | None = None
    requestOrdinal: int = Field(ge=0)
    # API 식별자는 id이고, ticketCode는 화면에 보여주는 짧은 번호다.
    ticketCode: str | None = Field(default=None, max_length=32)
    sourceChannel: SourceChannel
    senderDisplay: str | None = None
    occurredAt: datetime
    aiProcessingStatus: ProcessingStatus = "PENDING"
    summaryTitle: str | None = Field(default=None, max_length=80)
    aiDecisionStatus: AiDecisionStatus | None = None
    # 사람이 검증할 수 있는 짧은 판단 이유. 내부 추론 과정은 저장하지 않는다.
    decisionReason: str | None = Field(default=None, max_length=200)
    category: TicketCategory = "일반 질문"
    requirement: str = Field(default="", max_length=500)
    currentSummary: str = Field(default="", max_length=1000)
    ticketStatus: TicketStatus = "active"
    requestEvidence: list[RequestEvidence] = Field(default_factory=list)
    documentEvidence: list[DocumentEvidence] = Field(default_factory=list)
    # 한 번 만들면 저장해 둔다. 화면 진입마다 다시 만들지 않는다.
    solution: TicketSolution | None = None
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "client_requests"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("occurredAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("ticketStatus", ASCENDING)]),
            IndexModel(
                [("ownerId", ASCENDING), ("ticketCode", ASCENDING)],
                unique=True,
                partialFilterExpression={"ticketCode": {"$type": "string"}},
            ),
            IndexModel([("ownerId", ASCENDING), ("sourceMessageId", ASCENDING), ("requestOrdinal", ASCENDING)], unique=True),
        ]


def ticket_code(item: ClientRequest) -> str:
    """기존 문서는 코드가 없으므로 ObjectId에서 안정적인 표시값을 만든다."""

    return item.ticketCode or f"TCK-{str(item.id)[-6:].upper()}"


def public_client_request(item: ClientRequest) -> dict:
    return ClientRequestSummary(
        requestId=str(item.id), ticketId=str(item.id), ticketCode=ticket_code(item),
        projectId=str(item.projectId), sourceChannel=item.sourceChannel,
        senderDisplay=item.senderDisplay, occurredAt=item.occurredAt,
        aiProcessingStatus=item.aiProcessingStatus, summaryTitle=item.summaryTitle,
        aiDecisionStatus=item.aiDecisionStatus, ticketStatus=item.ticketStatus,
        category=item.category, requirement=item.requirement,
        currentSummary=item.currentSummary or item.decisionReason or "",
        createdAt=item.createdAt, updatedAt=item.updatedAt,
    ).model_dump(mode="json")
