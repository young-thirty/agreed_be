"""답변 초안 서브 에이전트. DATA_AI_PIPELINE.md §5 7단계.

사람이 고른 체크리스트 항목만 입력으로 받는다. 전체 대화나 계약 원문을 다시
넘기지 않는다 — 이미 판단이 끝난 항목만 반영해야, 모델이 사람이 지운 항목을
스스로 되살리는 일이 없다.

발송은 하지 않는다. 이 함수는 초안 문자열만 만든다.
"""

from infra.llm.harness import run_json
from infra.llm.prompts import REPLY_DRAFT_SYSTEM_PROMPT, resolve_reply_style
from infra.llm.schemas import ReplyDraftResult

FALLBACK_BODY = "요청 내용을 확인했습니다. 세부 사항은 확인 후 다시 안내드리겠습니다."


async def build_reply_draft(
    *, summary_title: str, selected_items: list[str], tone: str = "professional"
) -> str:
    style = resolve_reply_style(tone)
    if style is None:
        raise ValueError("지원하지 않는 답변 말투입니다.")
    items_text = "\n".join(f"- {item}" for item in selected_items) or "(선택된 확인 항목 없음)"
    task = f"요청 요약: {summary_title}\n말투 지침: {style}\n\n반영할 확인 항목:\n{items_text}"

    result = await run_json(
        system_prompt=REPLY_DRAFT_SYSTEM_PROMPT,
        user_content=task,
        schema=ReplyDraftResult,
    )
    return result.body.strip() if result and result.body.strip() else FALLBACK_BODY
