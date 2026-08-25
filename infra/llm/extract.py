"""요구사항 추출 파이프라인. L1 → L2 → L3을 여기서 잇는다.

L0(발화 분할)은 호출부(infra/ingest)가 이미 끝낸 상태로 받는다.
L4(사람 승인)는 core/contract_ops.py가 담당한다.
"""

import json
from collections.abc import Sequence

from core.domain import Evidence, RequirementState
from core.grounding import ground_evidence
from core.state_machine import demote
from infra.llm.client import EXTRACT_MODEL, get_client, has_api_key
from infra.llm.fallback import build_fallback_result
from infra.llm.prompts import EXTRACT_SYSTEM_PROMPT, build_conversation_text
from infra.llm.schemas import ExtractResult
from models.requirement import Requirement


async def _call_model(utterances: Sequence, retry_hint: str | None = None) -> ExtractResult:
    """L1: JSON mode로 받아 Pydantic으로 검증한다."""
    conversation = build_conversation_text(utterances)
    if retry_hint:
        conversation += (
            f"\n\n[이전 응답이 검증에 실패했다: {retry_hint}. 스키마에 맞게 다시 출력해라.]"
        )

    response = await get_client().chat.completions.create(
        model=EXTRACT_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": conversation},
        ],
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("모델이 빈 응답을 반환했습니다.")
    return ExtractResult.model_validate(json.loads(content))


async def _extract_with_retry(utterances: Sequence) -> ExtractResult:
    """검증에 실패하면 오류를 덧붙여 1회만 재시도한다. 그래도 실패하면 빈 결과다.

    무한 재시도는 하지 않는다. 사용자를 오래 기다리게 하지 않기 위해서다.
    """
    try:
        return await _call_model(utterances)
    except Exception as first_error:
        try:
            return await _call_model(utterances, retry_hint=str(first_error))
        except Exception:
            return ExtractResult(items=[])


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
        result = await _extract_with_retry(utterances)

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
