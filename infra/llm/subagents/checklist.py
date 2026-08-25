"""체크리스트 서브 에이전트. DATA_AI_PIPELINE.md §5 6단계.

답변 전에 사람이 확인할 항목을 만든다. 도구가 없는 단발 호출이다 — 요청과
계약 요약만 보고 물을 거리를 뽑는 일이라 스스로 자료를 더 찾아볼 필요가 없다.
"""

from infra.llm.harness import run_json
from infra.llm.prompts import CHECKLIST_SYSTEM_PROMPT
from infra.llm.schemas import ChecklistResult

# 모델을 못 쓰거나 실패했을 때 내려가는 항목. 빈 화면보다 낫다.
FALLBACK_ITEMS = ["요청하신 범위와 일정을 다시 확인해 주세요."]

_MAX_ITEMS = 5


async def build_checklist(*, summary_title: str, reason: str, request_quote: str) -> list[str]:
    task = (
        f"요청 요약: {summary_title}\n"
        f"판정 이유: {reason or '(없음)'}\n"
        f"요청 원문 인용: {request_quote or '(없음)'}"
    )
    result = await run_json(
        system_prompt=CHECKLIST_SYSTEM_PROMPT,
        user_content=task,
        schema=ChecklistResult,
    )
    if result is None:
        return FALLBACK_ITEMS

    items = [item.strip() for item in result.items if item.strip()][:_MAX_ITEMS]
    return items or FALLBACK_ITEMS
