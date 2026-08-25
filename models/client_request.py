from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from core.project_data import (
    AiDecisionStatus, ClientRequestSummary, DocumentEvidence, ProcessingStatus,
    RequestEvidence, ResponseStatus, SourceChannel,
)


def utc_now() -> datetime:
    return datetime.utcnow()


class ClientRequest(Document):
    ownerId: PydanticObjectId
    projectId: PydanticObjectId
    sourceMessageId: PydanticObjectId
    analysisRunId: PydanticObjectId | None = None
    requestOrdinal: int = Field(ge=0)
    sourceChannel: SourceChannel
    senderDisplay: str | None = None
    occurredAt: datetime
    aiProcessingStatus: ProcessingStatus = "PENDING"
    summaryTitle: str | None = Field(default=None, max_length=80)
    aiDecisionStatus: AiDecisionStatus | None = None
    responseStatus: ResponseStatus = "WAITING"
    requestEvidence: list[RequestEvidence] = Field(default_factory=list)
    documentEvidence: list[DocumentEvidence] = Field(default_factory=list)
    createdAt: datetime = Field(default_factory=utc_now)
    updatedAt: datetime = Field(default_factory=utc_now)

    class Settings:
        name = "client_requests"
        indexes = [
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("occurredAt", DESCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("responseStatus", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("sourceMessageId", ASCENDING), ("requestOrdinal", ASCENDING)], unique=True),
        ]


def public_client_request(item: ClientRequest) -> dict:
    return ClientRequestSummary(
        requestId=str(item.id), projectId=str(item.projectId), sourceChannel=item.sourceChannel,
        senderDisplay=item.senderDisplay, occurredAt=item.occurredAt,
        aiProcessingStatus=item.aiProcessingStatus, summaryTitle=item.summaryTitle,
        aiDecisionStatus=item.aiDecisionStatus, responseStatus=item.responseStatus,
    ).model_dump(mode="json")
