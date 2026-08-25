"""티켓 솔루션 오케스트레이터.

티켓 하나를 받아 여러 에이전트를 돌리고 하나의 솔루션으로 종합한다.
infra/llm/orchestrator.py가 "원문 한 건 → 요청 N건"을 맡는다면, 이 파일은
"티켓 한 건 → 솔루션 한 벌"을 맡는다.

    ┌ 계약 범위 대조   contract_match   (도구 있음)
    ├ 개발 현황        dev_status       (도구 있음, 저장소 clone)
    └ 이후: 영향 분석 · 작업 가능 여부   (도구 없음, 위 결과를 받아 판단)
                        ↓
                   솔루션 종합

조정은 코드가 하고 판단만 모델에 맡긴다. "근거를 대조하고, 현황을 보고,
영향을 따지고, 종합한다"는 순서는 규칙이지 추론이 아니다.

DB 쓰기는 여기서 하지 않는다. 값만 돌려주고 저장은 호출부가 한다.
"""

import asyncio

from beanie import PydanticObjectId

from core.project_data import (
    AiDecisionStatus, DevelopmentStatus, Feasibility, ImpactAnalysis,
)
from infra.llm.harness import run_json
from infra.llm.prompts import SOLUTION_SYNTHESIS_SYSTEM_PROMPT
from infra.llm.schemas import SolutionSynthesisResult
from infra.llm.subagents.contract_match import FALLBACK_DECISION, match_against_contract
from infra.llm.subagents.dev_status import build_development_status
from infra.llm.subagents.impact import build_feasibility, build_impact_analysis


class SolutionDraft:
    """저장 직전의 솔루션 재료. Document가 아니라 값 묶음이다."""

    def __init__(
        self,
        *,
        scope_decision: AiDecisionStatus,
        basis_quote: str,
        basis_document_id: str,
        development_status: DevelopmentStatus | None,
        impact_analysis: ImpactAnalysis | None,
        feasibility: Feasibility,
        advice_message: str,
        advice_reason: str,
        reply_draft: str,
    ) -> None:
        self.scopeDecision = scope_decision
        self.basisQuote = basis_quote
        self.basisDocumentId = basis_document_id
        self.developmentStatus = development_status
        self.impactAnalysis = impact_analysis
        self.feasibility = feasibility
        self.adviceMessage = advice_message
        self.adviceReason = advice_reason
        self.replyDraft = reply_draft


_NO_ADVICE = "자동 조언을 만들지 못했습니다. 원문과 계약을 직접 확인해 주세요."

_DECISION_LABEL = {
    "IN_SCOPE_ACTION_REQUIRED": "계약 범위 안. 그대로 진행할 수 있다",
    "OUT_OF_SCOPE_COORDINATION_REQUIRED": "범위가 애매하거나 근거가 부족하다. 확인이 필요하다",
    "EXTRA_REQUEST": "계약 밖 추가 요청. 비용·일정 협의가 필요하다",
}


def _synthesis_task(
    *,
    summary_title: str,
    requirement: str,
    decision: AiDecisionStatus,
    basis_quote: str,
    development_status: DevelopmentStatus | None,
    impact: ImpactAnalysis | None,
    feasibility: Feasibility,
) -> str:
    parts = [
        f"## 클라이언트 요청\n{summary_title}\n{requirement}",
        f"## 계약 범위 판정\n{_DECISION_LABEL.get(decision, decision)}",
        f"## 계약·자료 근거\n{basis_quote or '확인된 근거 없음'}",
    ]
    if development_status is not None:
        parts.append(
            "## 현재 구현 상태\n"
            f"- 대상 기능: {development_status.targetFeature or '확인 못 함'}\n"
            f"- 상태: {development_status.currentState or '확인 못 함'}"
        )
    else:
        parts.append("## 현재 구현 상태\n연결된 저장소가 없어 확인하지 못했다")

    if impact is not None:
        parts.append(
            "## 영향 범위\n"
            f"- 코드: {', '.join(impact.codeAreas) or '확인 못 함'}\n"
            f"- 화면: {', '.join(impact.screens) or '확인 못 함'}\n"
            f"- 기존 기능 영향: {impact.existingFeatureImpact or '확인 못 함'}"
        )
    parts.append(
        "## 작업 가능 여부\n"
        f"- 판단: {feasibility.verdict}\n"
        f"- 이유: {feasibility.reason or '없음'}"
    )
    return "\n\n".join(parts)


async def build_solution(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    summary_title: str,
    requirement: str = "",
    request_quote: str = "",
    raw_text: str = "",
) -> SolutionDraft:
    """티켓 하나의 솔루션을 만든다. 어느 단계가 실패해도 나머지는 살린다."""

    # 1차: 서로를 기다릴 이유가 없는 둘을 함께 돌린다.
    contract_task = match_against_contract(
        owner_id=owner_id,
        project_id=project_id,
        summary_title=summary_title,
        request_quote=request_quote,
        raw_text=raw_text or requirement or summary_title,
    )
    status_task = build_development_status(
        owner_id=owner_id,
        project_id=project_id,
        summary_title=summary_title,
        requirement=requirement,
    )
    contract, status = await asyncio.gather(
        contract_task, status_task, return_exceptions=True
    )
    if isinstance(contract, BaseException) or contract is None:
        decision, basis_quote, basis_document_id, reason = (
            FALLBACK_DECISION, "", "", "확인 가능한 근거가 부족합니다."
        )
    else:
        decision = contract.decision
        basis_quote = contract.documentQuote
        basis_document_id = contract.documentId
        reason = contract.reason
    if isinstance(status, BaseException):
        status = None

    # 2차: 개발 현황을 읽기 전용 컨텍스트로 받아 함께 돌린다.
    impact, feasibility = await asyncio.gather(
        build_impact_analysis(
            summary_title=summary_title, requirement=requirement, development_status=status
        ),
        build_feasibility(
            summary_title=summary_title, requirement=requirement, development_status=status
        ),
        return_exceptions=True,
    )
    if isinstance(impact, BaseException):
        impact = None
    if isinstance(feasibility, BaseException) or feasibility is None:
        feasibility = Feasibility(
            verdict="needs_clarification",
            reason="자동 판단을 하지 못했습니다. 직접 확인이 필요합니다.",
        )

    # 종합. 앞선 결과만 재료로 쓴다.
    synthesis = await run_json(
        system_prompt=SOLUTION_SYNTHESIS_SYSTEM_PROMPT,
        user_content=_synthesis_task(
            summary_title=summary_title,
            requirement=requirement,
            decision=decision,
            basis_quote=basis_quote,
            development_status=status,
            impact=impact,
            feasibility=feasibility,
        ),
        schema=SolutionSynthesisResult,
    )
    return SolutionDraft(
        scope_decision=decision,
        basis_quote=basis_quote,
        basis_document_id=basis_document_id,
        development_status=status,
        impact_analysis=impact,
        feasibility=feasibility,
        advice_message=synthesis.adviceMessage if synthesis else _NO_ADVICE,
        advice_reason=synthesis.adviceReason if synthesis else reason,
        reply_draft=synthesis.replyDraft if synthesis else "",
    )
