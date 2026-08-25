"""확인 질문과 답변 초안 생성.

추출(extract.py)과 같은 계층이다. 도메인 규칙은 없고, 모델을 부르고
스키마로 검증해 돌려주기만 한다.

금액·일정·수락 여부를 여기서 정하지 않는다. 프롬프트가 모델에게도 같은
제약을 건다. 사람이 결정할 것을 AI가 먼저 확정해버리면 안 되기 때문이다.
"""

import json
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel

from infra.llm.client import EXTRACT_MODEL, get_client
from infra.llm.prompts import CLARIFICATION_SYSTEM_PROMPT, REPLY_SYSTEM_PROMPT
from infra.llm.schemas import ClarificationQuestionsResult, ReplyDraftResult

_Result = TypeVar("_Result", bound=BaseModel)


async def _ask(system: str, user: str, schema: type[_Result]) -> _Result:
    """JSON mode로 받아 Pydantic으로 검증한다. 실패하면 호출부가 처리한다."""
    response = await get_client().chat.completions.create(
        model=EXTRACT_MODEL,
        response_format={"type": "json_object"},
        # 같은 요구사항에 매번 다른 질문이 나오면 사람이 화면을 믿기 어렵다.
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("모델이 빈 응답을 반환했습니다.")
    return schema.model_validate(json.loads(content))


async def build_questions(requirement_text: str) -> list[str]:
    """답변 전에 클라이언트에게 되물을 확인 질문을 만든다."""
    result = await _ask(
        CLARIFICATION_SYSTEM_PROMPT, requirement_text, ClarificationQuestionsResult
    )
    return result.questions


async def build_reply(
    requirement_text: str,
    *,
    tone: str,
    questions: Sequence[str],
    intent: str | None = None,
    decision: object | None = None,
) -> str:
    """고른 확인 질문·말투·사람이 정한 방향으로 답변 초안을 만든다.

    intent는 사람이 이 요구사항을 어떤 상태로 확정할지 정한 값이다. 무엇을
    말할지는 이 값이 정하고, 말투는 어떻게 말할지만 정한다.

    decision은 사람이 채운 금액·납기다. 있으면 초안이 빈 자리 대신 이 값을 쓴다.
    """
    numbered = "\n".join(f"{order}. {text}" for order, text in enumerate(questions, start=1))
    if decision is None:
        settled = "없음"
    else:
        note = getattr(decision, "note", None)
        settled = (
            f"- 금액 변동: {getattr(decision, 'amountDelta', 0)}원\n"
            f"- 납기: {getattr(decision, 'dueDate', '')}"
            + (f"\n- 메모: {note}" if note else "")
        )
    user = (
        f"{requirement_text}\n\n"
        f"## 말투\n{tone}\n\n"
        f"## 사람이 정한 방향\n{intent or '없음'}\n\n"
        f"## 사람이 정한 금액·납기\n{settled}\n\n"
        f"## 담을 확인 질문\n{numbered or '없음'}"
    )
    result = await _ask(REPLY_SYSTEM_PROMPT, user, ReplyDraftResult)
    return result.draft
