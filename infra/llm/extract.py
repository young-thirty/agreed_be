"""요구사항 추출 파이프라인. L1 → L2 → L3을 여기서 잇는다.

L0(발화 분할)은 호출부(infra/ingest)가 이미 끝낸 상태로 받는다.
L4(사람 승인)는 core/contract_ops.py가 담당한다.
"""

from collections.abc import Sequence

from core.domain import Evidence, RequirementState
from core.grounding import ground_evidence
from core.state_machine import demote
from infra.llm.client import has_api_key
from infra.llm.fallback import build_fallback_result
from infra.llm.harness import run_json
from infra.llm.prompts import EXTRACT_SYSTEM_PROMPT, build_conversation_text
from infra.llm.schemas import ExtractResult
from models.requirement import Requirement


async def _extract(utterances: Sequence) -> ExtractResult:
    """L1: JSON mode로 받아 Pydantic으로 검증한다.

    검증 실패 시 1회 재시도하고 그래도 안 되면 빈 결과로 넘어가는 규칙은
    infra/llm/harness.py가 담당한다. 무한 재시도는 하지 않는다.
    """
    result = await run_json(
        system_prompt=EXTRACT_SYSTEM_PROMPT,
        user_content=build_conversation_text(utterances),
        schema=ExtractResult,
    )
    return result or ExtractResult(items=[])


async def extract_requirements(
    utterances: Sequence,
    existing: Sequence[Requirement] = (),
) -> list[RequirementState]:
    """대화에서 요구사항을 뽑아 검증까지 마친 목록을 돌려준다.

    existing은 재분석 대상이다. 모델이 existingId로 기존 카드를 가리키면 그
    카드의 현재 status를 demote의 기준으로 쓴다. 신규 항목은 '미확정'에서
    시작한다. 사람이 이미 확정한 decision은 재분석으로 지워지지 않는다.
    """
    if not utterances:
        return []

    result = build_fallback_result(utterances)
    if result is None:
        if not has_api_key():
            return []
        result = await _extract(utterances)

    existing_by_id = {str(r.id): r for r in existing}
    requirements: list[RequirementState] = []

    for item in result.items:
        # L2: 근거가 전부 허구면 항목을 버린다. 일부만 실패하면 나머지는 살린다.
        grounded = ground_evidence(
            utterances,
            [Evidence(utteranceIndex=e.utteranceIndex, quote=e.quote) for e in item.evidence],
        )
        if not grounded:
            continue

        previous = existing_by_id.get(item.existingId) if item.existingId else None
        current_status = previous.status if previous else "미확정"

        requirements.append(
            RequirementState(
                title=item.title,
                # L3: 이전 상태에서 도달 불가능하면 거부하지 않고 '미확정'으로 내린다.
                status=demote(current_status, item.proposedStatus),
                evidence=grounded,
                basis=previous.basis if previous else {"kind": "없음"},
                aiProposedDecision=(
                    None
                    if item.proposedDecision is None
                    else item.proposedDecision.model_dump()
                ),
                decision=previous.decision if previous else None,
            )
        )

    return requirements
