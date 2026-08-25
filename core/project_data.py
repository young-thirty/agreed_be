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
# 티켓 상태. pending은 두지 않는다 — active와의 경계가 사람마다 달라
# 아무도 쓰지 않는 상태가 된다. 전이는 사람만 하고 AI는 제안하지 않는다.
TicketStatus = Literal["active", "done", "rejected"]
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
    ticketStatus: TicketStatus


class ProjectMaterialSummary(BaseModel):
    materialId: str
    projectId: str
    fileName: str
    direction: Direction
    communicatedAt: datetime
    classificationStatus: ProcessingStatus
    documentType: DocumentType | None = None


class RelatedFile(BaseModel):
    """조언의 판단에 쓰인 프로젝트 자료. AI가 고르지 않고 DB가 정한다."""

    materialId: str
    fileName: str
    documentType: DocumentType | None = None


class TicketSolution(BaseModel):
    """티켓 하나에 붙는 AI 산출물 묶음. 이 제품의 결과물이다.

    조언과 근거는 한 번 만들어 저장한다. 답변 초안은 여기 담지 않는다 — 사람이
    말투를 바꿔가며 여러 번 보는 값이라, 모든 스타일을 미리 만들면 쓰지도 않을
    초안에 토큰을 쓰게 된다. 고른 스타일 하나만 그때 만든다.
    """

    adviceMessage: str
    adviceReason: str = ""
    basisQuote: str = ""
    basisDocumentId: str = ""
    relatedFiles: list[RelatedFile] = Field(default_factory=list)
    generatedAt: datetime
