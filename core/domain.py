"""공유 도메인 타입의 단일 원천이다.

여기에는 Beanie도 FastAPI도 등장하지 않는다. 순수한 값 객체만 둔다.
영속성은 models/가 이 클래스들을 상속해서 얹는다.

필드 이름을 camelCase로 쓰는 이유는 하나다. 프론트엔드(Next.js)의
types/index.ts가 이미 이 이름으로 응답을 기대하고 있어서, 여기서 snake_case를
쓰면 직렬화 별칭 설정이 한 겹 더 필요해진다. 그 설정이 어긋나면 조용히
필드가 사라지므로, 이름을 그대로 맞추는 쪽을 택했다.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

# --- 요구사항 상태 -------------------------------------------------------

RequirementStatus = Literal[
    "미확정",
    "문의",
    "요청",
    "제안",
    "내부검토",
    "고객검토",
    "합의",
    "거절",
    "완료",
]

REQUIREMENT_STATUS: tuple[RequirementStatus, ...] = (
    "미확정",
    "문의",
    "요청",
    "제안",
    "내부검토",
    "고객검토",
    "합의",
    "거절",
    "완료",
)

# --- 대화 ----------------------------------------------------------------

# 카카오톡은 공개 API가 없어 자동 수집이 불가능하므로 채널로 두지 않는다.
Channel = Literal["이메일", "슬랙"]


class Utterance(BaseModel):
    """발화 단위. L0(발화 분할)의 산출물이다.

    index가 있어야 L2 근거 검증과 화면의 원문 하이라이트가 가능하다.
    """

    index: int
    channel: Channel
    speaker: str
    text: str


class Evidence(BaseModel):
    """요구사항 카드가 원문 어디에 근거하는지. L2가 인용문을 원문과 대조한다."""

    utteranceIndex: int
    quote: str


# --- 계약 근거 -----------------------------------------------------------


class ContractBasis(BaseModel):
    kind: Literal["계약서"]
    clause: str


class ProposalBasis(BaseModel):
    """제안서에만 있고 계약서에는 없는 경우. 프리랜서가 가장 헷갈리는 지점이다."""

    kind: Literal["제안서"]
    clause: str


class NoBasis(BaseModel):
    kind: Literal["없음"] = "없음"


Basis = Annotated[
    Union[ContractBasis, ProposalBasis, NoBasis],
    Field(discriminator="kind"),
]


# --- 판단 ----------------------------------------------------------------


class Decision(BaseModel):
    """금액·일정 결정.

    RequirementState에서 aiProposedDecision과 decision 두 자리로 나뉜다.
    앞은 AI가 대화 근거로 채워보는 초안이고, 뒤는 사람이 확정한 값이다.
    계약에 반영되는 것은 decision뿐이다.
    """

    amountDelta: int
    dueDate: str
    note: str | None = None


# --- 계약 ----------------------------------------------------------------


class ContractState(BaseModel):
    version: int
    scope: list[str]
    dueDate: str
    amount: int


class ContractDiff(BaseModel):
    """계약 버전 간 변경분. 화면의 diff 표시가 이 값을 그대로 그린다."""

    scopeAdded: list[str]
    scopeRemoved: list[str]
    dueDateChanged: dict[str, str] | None
    amountDelta: int


# --- 요구사항 ------------------------------------------------------------


class RequirementState(BaseModel):
    title: str
    status: RequirementStatus
    evidence: list[Evidence]
    basis: Basis
    aiProposedDecision: Decision | None = None
    decision: Decision | None = None
