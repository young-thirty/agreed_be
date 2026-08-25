"""프로젝트 화면과 수집·AI 파이프라인이 공유하는 순수 타입."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["ACTIVE", "DRAFT", "COMPLETED"]
ProjectSort = Literal["status", "updatedAt", "createdAt"]
SourceChannel = Literal["GMAIL", "SLACK", "GITHUB"]
ProcessingStatus = Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]
AiDecisionStatus = Literal[
    "IN_SCOPE_ACTION_REQUIRED",
    "OUT_OF_SCOPE_COORDINATION_REQUIRED",
    "EXTRA_REQUEST",
]
ResponseStatus = Literal["WAITING", "COMPLETED"]
Direction = Literal["RECEIVED", "SENT"]
DocumentType = Literal["PROPOSAL", "CONTRACT", "REQUIREMENTS", "MEETING_NOTES", "OTHER"]
MaterialOrigin = Literal["CHANNEL", "MANUAL"]
AnalysisTargetType = Literal["CLIENT_REQUEST", "MATERIAL_CLASSIFICATION"]


class RequestEvidence(BaseModel):
    quote: str = Field(min_length=1, max_length=500)
    sourceMessageId: str


class DocumentEvidence(BaseModel):
    quote: str = Field(min_length=1, max_length=500)
    documentId: str


class ProjectSummary(BaseModel):
    projectId: str
    name: str
    clientName: str
    startDate: date | None = None
    endDate: date | None = None
    contractPrice: int | None = None
    unansweredRequestCount: int = 0
    createdAt: datetime
    updatedAt: datetime
    status: ProjectStatus


class ClientRequestSummary(BaseModel):
    requestId: str
    projectId: str
    sourceChannel: SourceChannel
    senderDisplay: str | None = None
    occurredAt: datetime
    aiProcessingStatus: ProcessingStatus
    summaryTitle: str | None = None
    aiDecisionStatus: AiDecisionStatus | None = None
    responseStatus: ResponseStatus


class ProjectMaterialSummary(BaseModel):
    materialId: str
    projectId: str
    fileName: str
    direction: Direction
    communicatedAt: datetime
    classificationStatus: ProcessingStatus
    documentType: DocumentType | None = None
