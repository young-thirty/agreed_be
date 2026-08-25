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


class ClarificationQuestionsResult(BaseModel):
    """답변 전에 클라이언트에게 되물을 확인 질문."""

    questions: list[str] = Field(min_length=1, max_length=6)


class RequirementReplyResult(BaseModel):
    """요구사항 하나를 놓고 만든 답변 초안. 사람이 그대로 읽고 고칠 수 있어야 한다.

    아래 ReplyDraftResult(요청 단위)와 쓰임이 다르다. 이름이 겹치면 뒤 정의가
    앞 정의를 조용히 덮으므로 갈라 둔다.
    """

    draft: str = Field(min_length=1, max_length=4000)


class RequestAnalysisResult(BaseModel):
    summaryTitle: str = Field(max_length=80)
    decision: AiDecisionStatus
    quote: str = Field(default="", max_length=500)


class MaterialClassificationResult(BaseModel):
    documentType: DocumentType


# --- 서브 에이전트 출력 -------------------------------------------------------
#
# 아래 스키마는 infra/llm/subagents/가 쓴다. 모델이 무엇을 낼 수 있는지를
# 여기서 좁혀두면, 하네스가 받은 결과를 그대로 신뢰하지 않고 한 번 더 거를 수 있다.


class ExtractedRequest(BaseModel):
    """원문 한 건에서 뽑아낸 클라이언트 요청 하나."""

    summaryTitle: str = Field(max_length=80)
    quote: str = Field(default="", max_length=500)


class RequestExtractionResult(BaseModel):
    """원문 한 건에 요청이 여러 개 있을 수 있다. 없으면 빈 목록이다.

    상한을 두는 이유는 모델이 한 문장을 여러 요청으로 잘게 쪼개는 경우가 있어서다.
    """

    requests: list[ExtractedRequest] = Field(default_factory=list, max_length=5)


class ContractMatchResult(BaseModel):
    """계약 대조 서브 에이전트의 결론.

    documentQuote는 모델이 도구 결과에서 옮겨 적은 근거 조항이다. 코드가 실제
    문서에 있는지 다시 확인하고, 없으면 버린 뒤 판정을 주황으로 내린다.
    """

    decision: AiDecisionStatus
    reason: str = Field(default="", max_length=200)
    documentQuote: str = Field(default="", max_length=500)
    documentId: str = Field(default="", max_length=64)


class ChecklistResult(BaseModel):
    """답변 전에 사람이 확인할 항목. 사람이 고르고 지우고 더한다."""

    items: list[str] = Field(default_factory=list, max_length=6)


class ReplyDraftResult(BaseModel):
    """고객에게 보낼 답변 초안. 생성만 하고 발송하지 않는다."""

    body: str = Field(max_length=3000)
