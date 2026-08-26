"""프로젝트 화면과 수집·AI 파이프라인이 공유하는 순수 타입."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["ACTIVE", "DRAFT", "COMPLETED", "REJECTED"]
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
TicketCategory = Literal[
    "기능 요청", "버그", "일반 질문", "계약 문의", "일정 문의", "디자인 수정",
]
TicketHandling = Literal["link", "create", "ignore"]
WorkStage = Literal["to_analyze", "to_reply", "waiting", "idle"]
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
    ticketId: str
    ticketCode: str
    projectId: str
    sourceChannel: SourceChannel
    senderDisplay: str | None = None
    occurredAt: datetime
    aiProcessingStatus: ProcessingStatus
    summaryTitle: str | None = None
    aiDecisionStatus: AiDecisionStatus | None = None
    ticketStatus: TicketStatus
    category: TicketCategory
    requirement: str
    currentSummary: str
    createdAt: datetime
    updatedAt: datetime


class ProjectMaterialSummary(BaseModel):
    materialId: str
    projectId: str
    ticketId: str | None = None
    fileName: str
    direction: Direction
    communicatedAt: datetime
    classificationStatus: ProcessingStatus
    documentType: DocumentType | None = None
    summary: str | None = None


class RelatedFile(BaseModel):
    """조언의 판단에 쓰인 프로젝트 자료. AI가 고르지 않고 DB가 정한다."""

    materialId: str
    fileName: str
    documentType: DocumentType | None = None
    summary: str | None = None


FeasibilityVerdict = Literal[
    "feasible",
    "feasible_with_scope_change",
    "needs_clarification",
    "blocked",
]


class DevelopmentStatus(BaseModel):
    """연결된 저장소에서 읽어낸 현재 구현 상태. 코드를 고치지 않고 읽기만 한다."""

    targetFeature: str = ""
    currentState: str = ""
    relatedPaths: list[str] = Field(default_factory=list)
    relatedRefs: list[str] = Field(default_factory=list)


class ImpactAnalysis(BaseModel):
    """요청이 건드리는 범위. 파일이나 DB를 실제로 바꾸지 않는다."""

    codeAreas: list[str] = Field(default_factory=list)
    screens: list[str] = Field(default_factory=list)
    dataModels: list[str] = Field(default_factory=list)
    authImpact: str = ""
    existingFeatureImpact: str = ""
    testScope: list[str] = Field(default_factory=list)


class Feasibility(BaseModel):
    """기술적으로 만들 수 있는가. 계약 범위 판정(AiDecisionStatus)과 다른 축이다.

    계약 밖이어도 기술적으로는 쉬울 수 있고, 계약 안이어도 막힐 수 있다.
    금액·납기·합의는 여기서 정하지 않는다.
    """

    verdict: FeasibilityVerdict = "needs_clarification"
    reason: str = ""
    requiredHumanInput: list[str] = Field(default_factory=list)


class TicketSolution(BaseModel):
    """티켓 하나에 붙는 AI 산출물 묶음. 이 제품의 결과물이다.

    조언과 근거는 한 번 만들어 저장한다.

    replyDraft는 AI가 만든 기본 초안 하나다. 사람이 말투를 바꿔 다시 만든 초안은
    여기 쌓지 않고 TicketDecision.drafts에 들어간다 — 이쪽은 AI 산출물이고
    저쪽은 사람이 고른 결과라 수명이 다르다.
    """

    adviceMessage: str
    adviceReason: str = ""
    basisQuote: str = ""
    basisDocumentId: str = ""
    relatedFiles: list[RelatedFile] = Field(default_factory=list)
    # 계약 범위 대조 결과. 티켓의 aiDecisionStatus와 같은 값이지만, 솔루션을
    # 만든 시점의 판정을 함께 남겨 나중에 달라졌는지 볼 수 있게 한다.
    scopeDecision: AiDecisionStatus | None = None
    developmentStatus: DevelopmentStatus | None = None
    impactAnalysis: ImpactAnalysis | None = None
    feasibility: Feasibility | None = None
    replyDraft: str = ""
    generatedAt: datetime
