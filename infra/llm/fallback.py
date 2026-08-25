"""시연 폴백.

고정 시연 시나리오를 감지하면 네트워크를 타지 않고 즉시 결과를 돌려준다.
현장에서 회선이 끊기거나 모델 응답이 흔들려도 시연이 멈추지 않게 하는 장치다.

이 시나리오가 아니면 None을 돌려주고, 호출부가 실제 모델 호출로 넘어간다.
"""

from collections.abc import Sequence

from core.domain import Utterance
from infra.llm.schemas import ExtractedEvidence, ExtractedItem, ExtractResult

_DEMO_MARKER = "영문 페이지도 가능할까요"


def build_fallback_result(utterances: Sequence[Utterance]) -> ExtractResult | None:
    for utterance in utterances:
        if _DEMO_MARKER in utterance.text:
            return ExtractResult(
                items=[
                    ExtractedItem(
                        title="영문 페이지 추가",
                        proposedStatus="문의",
                        evidence=[
                            ExtractedEvidence(
                                utteranceIndex=utterance.index,
                                quote=_DEMO_MARKER,
                            )
                        ],
                        existingId=None,
                        proposedDecision=None,
                    )
                ]
            )
    return None
