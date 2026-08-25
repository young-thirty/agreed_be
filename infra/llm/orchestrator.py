"""요청 분석 오케스트레이터.

원문 한 건을 받아 요청을 뽑고, 요청마다 계약 대조를 서브 에이전트에 위임한 뒤,
검증이 끝난 결과만 돌려준다. shookie의 메인 에이전트가 서브 에이전트에 위임하고
결과를 종합하는 것과 같은 자리다.

다만 우리는 슬랙 대화가 아니라 내부 파이프라인이므로 조정자까지 LLM에 맡기지
않는다. "원문에서 요청을 뽑고, 각 요청을 계약과 대조한다"는 순서는 규칙이지
추론이 아니기 때문이다. 조정은 이 파일의 코드가 하고, 판단만 모델에 맡긴다.

DB 쓰기는 여기서 하지 않는다. 값만 돌려주고 저장은 호출부가 한다.
"""

import asyncio

from beanie import PydanticObjectId
from pydantic import BaseModel

from core.grounding import is_quote_in
from core.project_data import AiDecisionStatus
from infra.llm.harness import run_json
from infra.llm.prompts import REQUEST_EXTRACT_SYSTEM_PROMPT
from infra.llm.schemas import RequestExtractionResult
from infra.llm.subagents.contract_match import FALLBACK_DECISION, match_against_contract

# 원문 한 건에서 만들 수 있는 요청 수의 상한. 스키마에도 상한이 있지만,
# 폴백 경로까지 포함해 한 번 더 잠근다.
MAX_REQUESTS_PER_MESSAGE = 5

# 모델에게 넘기는 원문 길이. 계약 대조 쪽에서 한 번 더 자른다.
_MAX_RAW_CHARS = 12000


class AnalyzedRequest(BaseModel):
    """검증까지 끝난 요청 한 건. 저장 직전의 값이다."""

    summaryTitle: str
    requestQuote: str = ""
    decision: AiDecisionStatus
    reason: str = ""
    documentQuote: str = ""
    documentId: str = ""


def _fallback_requests(raw_text: str) -> list[dict[str, str]]:
    """모델을 부르지 못했을 때 원문 첫 줄로 요청 하나를 만든다.

    시연 중 키가 없거나 네트워크가 끊겨도 화면에 카드가 남아야 한다. 대신 판정은
    올리지 않는다. 근거 없이 초록·빨강을 고르지 않는다는 원칙은 폴백에서도 같다.
    """
    first_line = next((line.strip() for line in raw_text.splitlines() if line.strip()), "")
    if not first_line:
        return []
    return [{"summaryTitle": first_line[:80], "quote": first_line[:500]}]


def _grounded_requests(
    extracted: RequestExtractionResult, raw_text: str
) -> list[dict[str, str]]:
    """L2: 인용이 원문에 실제로 있는지 확인한다. 부분 수용 원칙을 지킨다.

    인용이 허구여도 요청 자체를 버리지는 않는다. 제목은 살아 있을 수 있으므로
    인용만 비우고 넘긴다. 5건 중 1건이 실패하면 그 인용 하나만 버린다.
    """
    requests: list[dict[str, str]] = []
    for item in extracted.requests[:MAX_REQUESTS_PER_MESSAGE]:
        title = item.summaryTitle.strip()
        if not title:
            continue
        quote = item.quote if is_quote_in(raw_text, item.quote) else ""
        requests.append({"summaryTitle": title[:80], "quote": quote})
    return requests


async def analyze_request_message(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    raw_text: str,
) -> list[AnalyzedRequest]:
    """원문 한 건을 요청 0건 이상으로 분석한다.

    이전 구현은 원문 한 건을 요청 한 건으로 고정했다. 메일 하나에 요청이 셋이면
    셋을 하나로 뭉갰다는 뜻이다. 여기서는 뽑은 만큼 만들고, 각각을 따로 대조한다.

    계약 대조는 요청끼리 독립적이므로 함께 돌린다. 직렬로 돌리면 요청 3건에
    서브 에이전트 3회가 순서대로 붙어 시연에서 기다릴 수 없다.
    """

    if not raw_text.strip():
        return []

    extracted = await run_json(
        system_prompt=REQUEST_EXTRACT_SYSTEM_PROMPT,
        user_content=raw_text[:_MAX_RAW_CHARS],
        schema=RequestExtractionResult,
    )
    requests = _grounded_requests(extracted, raw_text) if extracted else []
    if not requests:
        requests = _fallback_requests(raw_text)
    if not requests:
        return []

    matches = await asyncio.gather(
        *(
            match_against_contract(
                owner_id=owner_id,
                project_id=project_id,
                summary_title=item["summaryTitle"],
                request_quote=item["quote"],
                raw_text=raw_text,
            )
            for item in requests
        ),
        return_exceptions=True,
    )

    analyzed: list[AnalyzedRequest] = []
    for item, match in zip(requests, matches):
        if isinstance(match, BaseException):
            # 대조 하나가 실패해도 나머지 요청은 살린다. 판정만 주황으로 둔다.
            analyzed.append(
                AnalyzedRequest(
                    summaryTitle=item["summaryTitle"],
                    requestQuote=item["quote"],
                    decision=FALLBACK_DECISION,
                    reason="확인 가능한 근거가 부족합니다.",
                )
            )
            continue
        analyzed.append(
            AnalyzedRequest(
                summaryTitle=item["summaryTitle"],
                requestQuote=item["quote"],
                decision=match.decision,
                reason=match.reason,
                documentQuote=match.documentQuote,
                documentId=match.documentId,
            )
        )
    return analyzed
