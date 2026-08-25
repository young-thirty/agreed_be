"""L1 스키마. 모델 출력을 이 스키마로 검증한다.

proposedStatus는 LLM_PROPOSABLE만 받는다. 합의·완료·거절은 여기에 없으므로
모델이 그 값을 내면 검증 단계에서 걸린다.

proposedDecision은 대화에 실제로 금액·날짜 근거가 있을 때만 모델이 채운다.
사람이 확정(Requirement.decision)하기 전까지는 참고용이고 계약에 반영되지 않는다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from core.state_machine import LLM_PROPOSABLE
from core.project_data import AiDecisionStatus, DocumentType

# LLM_PROPOSABLE를 Literal로 바꿔 스키마에 박는다. 상태 목록을 두 곳에
# 따로 적지 않기 위해서다.
ProposableStatus = Literal[LLM_PROPOSABLE]  # type: ignore[valid-type]


class ExtractedEvidence(BaseModel):
    utteranceIndex: int
    quote: str


class ExtractedDecision(BaseModel):
    amountDelta: int
    dueDate: str
    note: str | None = None


class ExtractedItem(BaseModel):
    title: str = Field(max_length=40)
    proposedStatus: ProposableStatus
    evidence: list[ExtractedEvidence] = Field(min_length=1)
    existingId: str | None = None
    proposedDecision: ExtractedDecision | None = None


class ExtractResult(BaseModel):
    items: list[ExtractedItem]


class RequestAnalysisResult(BaseModel):
    summaryTitle: str = Field(max_length=80)
    decision: AiDecisionStatus
    quote: str = Field(default="", max_length=500)


class MaterialClassificationResult(BaseModel):
    documentType: DocumentType
