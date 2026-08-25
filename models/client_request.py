from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import (
    AiDecisionStatus, ClientRequestSummary, DocumentEvidence, ProcessingStatus,
    RequestEvidence, SourceChannel, TicketSolution, TicketStatus,
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
    sourceChannel: SourceChannel
    senderDisplay: str | None = None
    occurredAt: datetime
    aiProcessingStatus: ProcessingStatus = "PENDING"
    summaryTitle: str | None = Field(default=None, max_length=80)
    aiDecisionStatus: AiDecisionStatus | None = None
    # 사람이 검증할 수 있는 짧은 판단 이유. 내부 추론 과정은 저장하지 않는다.
    decisionReason: str | None = Field(default=None, max_length=200)
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
            IndexModel([("ownerId", ASCENDING), ("sourceMessageId", ASCENDING), ("requestOrdinal", ASCENDING)], unique=True),
        ]


def public_client_request(item: ClientRequest) -> dict:
    return ClientRequestSummary(
        requestId=str(item.id), projectId=str(item.projectId), sourceChannel=item.sourceChannel,
        senderDisplay=item.senderDisplay, occurredAt=item.occurredAt,
        aiProcessingStatus=item.aiProcessingStatus, summaryTitle=item.summaryTitle,
        aiDecisionStatus=item.aiDecisionStatus, ticketStatus=item.ticketStatus,
    ).model_dump(mode="json")
