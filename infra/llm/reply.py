"""확인 질문과 답변 초안 생성.

추출(extract.py)과 같은 계층이다. 도메인 규칙은 없고, 모델을 부르고
스키마로 검증해 돌려주기만 한다. 호출은 infra/llm/harness.py를 거친다.

금액·일정·수락 여부를 여기서 정하지 않는다. 프롬프트가 모델에게도 같은
제약을 건다. 사람이 결정할 것을 AI가 먼저 확정해버리면 안 되기 때문이다.
"""

from collections.abc import Sequence

from infra.llm.harness import run_json
from infra.llm.prompts import CLARIFICATION_SYSTEM_PROMPT, REPLY_SYSTEM_PROMPT
from infra.llm.schemas import ClarificationQuestionsResult, RequirementReplyResult


async def build_questions(requirement_text: str) -> list[str]:
    """답변 전에 클라이언트에게 되물을 확인 질문을 만든다."""
    result = await run_json(
        system_prompt=CLARIFICATION_SYSTEM_PROMPT,
        user_content=requirement_text,
        schema=ClarificationQuestionsResult,
    )
    if result is None:
        raise ValueError("확인 질문을 만들지 못했습니다.")
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
    result = await run_json(
        system_prompt=REPLY_SYSTEM_PROMPT,
        user_content=user,
        schema=RequirementReplyResult,
    )
    if result is None:
        raise ValueError("답변 초안을 만들지 못했습니다.")
    return result.draft
