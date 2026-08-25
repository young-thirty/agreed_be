"""L2 근거 검증.

모델이 내놓은 인용문이 원문에 실제로 존재하는지 대조한다. 없으면 그 항목을
버린다. 화면에 뜨는 모든 카드가 원문의 특정 문장을 가리키게 만드는 장치다.
"""

import re
from collections.abc import Sequence

from core.domain import Evidence, Utterance

# 공백과 따옴표 종류만 지운다. 모델이 인용을 옮기면서 띄어쓰기나 따옴표를
# 바꾸는 일이 잦은데, 그것 때문에 멀쩡한 근거가 버려지면 안 된다.
_STRIP_PATTERN = re.compile(r"[\s'\"'']+")


def normalize(text: str) -> str:
    return _STRIP_PATTERN.sub("", text)


def is_grounded(utterances: Sequence[Utterance], evidence: Evidence) -> bool:
    """인용문이 해당 발화 원문에 실제로 존재하는지 확인한다."""
    for utterance in utterances:
        if utterance.index == evidence.utteranceIndex:
            return normalize(evidence.quote) in normalize(utterance.text)
    return False


def ground_evidence(
    utterances: Sequence[Utterance], evidence: Sequence[Evidence]
) -> list[Evidence]:
    """근거 없는 인용은 버리고 나머지는 살린다. 부분 수용 원칙.

    5건 중 1건이 실패하면 그 1건만 버리고 4건은 살린다. 하나도 안 남으면
    빈 리스트가 되고, 호출부가 그 항목 전체를 버릴지 판단한다.
    """
    return [e for e in evidence if is_grounded(utterances, e)]


def is_quote_in(text: str, quote: str) -> bool:
    """인용문이 문서 원문에 실제로 존재하는지 확인한다.

    is_grounded가 발화(Utterance)를 대상으로 하는 것과 같은 규칙을 계약 조항·자료
    본문에 적용한다. 모델이 계약서 문구를 옮기며 공백을 바꾸는 일이 잦아서
    정규화 후 비교한다. 빈 인용은 근거로 치지 않는다.
    """
    return bool(quote.strip()) and normalize(quote) in normalize(text)
