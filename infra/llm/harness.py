"""LLM 호출 하네스.

지금까지 호출 코드가 두 곳에 갈라져 있었다. infra/llm/extract.py의
``_extract_with_retry``는 1회 재시도, app/api/projects.py의 ``_llm_json``은 2회
루프로 같은 일을 따로 했고, 후자는 라우트 파일 안에 있어 계층 규칙에도
어긋났다. 둘을 여기로 합친다.

호출 방식은 두 가지다.

- ``run_json``: 단발 JSON mode. 대화에서 무엇을 뽑아내기만 하면 되는 L1 추출용이다.
  기존 규약(JSON mode + Pydantic 재검증 + 1회 재시도)을 그대로 유지한다.
- ``run_agent``: 도구를 주고 여러 턴을 돌리는 서브 에이전트용이다. "계약을 확인해
  보고 판단한다"처럼 필요한 자료를 스스로 더 찾아야 하는 판단에만 쓴다.

어느 쪽이든 실패는 예외가 아니라 ``None``으로 돌아온다. 화면을 깨뜨리지 않기
위해서다. 호출부는 ``None``을 받으면 안전한 쪽으로 강등한다.
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from infra.llm.client import EXTRACT_MODEL, get_client, has_api_key

T = TypeVar("T", bound=BaseModel)

# 서브 에이전트가 도구를 부를 수 있는 최대 턴 수. 넘으면 도구를 빼고 결론만
# 한 번 더 물어본다. 무한 루프는 프롬프트가 아니라 코드가 막는다.
MAX_AGENT_TURNS = 6

# 서브 에이전트 하나의 전체 예산(초). 턴당 타임아웃(client.py의 8초)과 별개다.
# 8초짜리 호출을 6턴 돌면 최악 48초가 되어 시연에서 기다릴 수 없다.
AGENT_BUDGET_SECONDS = 30.0

# 도구 하나가 돌려줄 수 있는 문자열 상한. 한 도구가 모델 컨텍스트를 다 먹지
# 않게 한다.
MAX_TOOL_RESULT_CHARS = 4000


@dataclass(frozen=True)
class AgentTool:
    """서브 에이전트에게 주는 도구 하나.

    ``run``은 모델이 넘긴 인자 dict를 받아 모델에게 돌려줄 문자열을 만든다.
    소유권 조건(ownerId, projectId)은 도구를 만드는 쪽이 클로저로 묶어 넣는다.
    모델이 소유자를 인자로 지정하게 두지 않는다.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str]]

    def to_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _validate(schema: type[T], content: str | None) -> T:
    if not content:
        raise ValueError("모델이 빈 응답을 반환했습니다.")
    return schema.model_validate(json.loads(content))


async def run_json(
    *,
    system_prompt: str,
    user_content: str,
    schema: type[T],
    temperature: float = 0,
) -> T | None:
    """단발 JSON mode 호출. 검증에 실패하면 오류를 덧붙여 1회만 재시도한다.

    temperature 기본값이 0인 이유가 있다. 지정하지 않으면 DeepSeek 기본값(1.0)으로
    도는데, 같은 대화에서도 결과가 흔들려 요구사항 추출이 0건과 1건 사이를
    오간다. 실측으로 확인한 값이라 취향이 아니다. 창작이 필요한 호출만 올린다.
    """

    if not has_api_key():
        return None

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await get_client().chat.completions.create(
            model=EXTRACT_MODEL,
            response_format={"type": "json_object"},
            temperature=temperature,
            messages=messages,
        )
        return _validate(schema, response.choices[0].message.content)
    except Exception as first_error:
        retry = [
            *messages,
            {
                "role": "user",
                "content": (
                    f"이전 응답이 검증에 실패했다: {first_error}. "
                    "설명을 덧붙이지 말고 스키마에 맞는 JSON만 다시 출력해라."
                ),
            },
        ]

    try:
        response = await get_client().chat.completions.create(
            model=EXTRACT_MODEL,
            response_format={"type": "json_object"},
            temperature=temperature,
            messages=retry,
        )
        return _validate(schema, response.choices[0].message.content)
    except Exception:
        return None


async def _run_tool(tool: AgentTool, raw_arguments: str) -> str:
    try:
        arguments = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError:
        return "도구 인자를 읽지 못했습니다. JSON 형식으로 다시 호출하세요."

    try:
        result = await tool.run(arguments)
    except Exception:
        # 도구 실패를 위로 던지지 않는다. 모델이 다른 방법을 시도할 여지를 남긴다.
        return "도구 실행에 실패했습니다. 다른 방법을 시도하세요."
    return result[:MAX_TOOL_RESULT_CHARS]


async def _dispatch(by_name: dict[str, AgentTool], name: str, raw_arguments: str) -> str:
    tool = by_name.get(name)
    if tool is None:
        return "존재하지 않는 도구입니다. 주어진 도구만 사용하세요."
    return await _run_tool(tool, raw_arguments)


async def _conclude(client, messages: list[dict[str, Any]], schema: type[T]) -> T | None:
    """도구를 빼고 결론만 JSON으로 받아 검증한다. 실패하면 1회 더 시도한다."""

    ask = {
        "role": "user",
        "content": (
            "지금까지 확인한 내용만으로 결론을 내라. 확인하지 못한 것은 지어내지 말고 "
            "근거 없음으로 처리해라. 설명을 덧붙이지 말고 스키마에 맞는 JSON만 출력해라."
        ),
    }
    for _ in range(2):
        try:
            response = await client.chat.completions.create(
                model=EXTRACT_MODEL,
                response_format={"type": "json_object"},
                messages=[*messages, ask],
            )
            return _validate(schema, response.choices[0].message.content)
        except Exception:
            continue
    return None


async def run_agent(
    *,
    system_prompt: str,
    task: str,
    tools: Sequence[AgentTool],
    schema: type[T],
    max_turns: int = MAX_AGENT_TURNS,
) -> T | None:
    """도구를 주고 결론이 나올 때까지 돌린다. 마지막 응답만 스키마로 검증한다.

    턴 예산이나 시간 예산이 끝나면 도구를 빼고 한 번 더 물어 결론을 받는다.
    도구만 계속 부르다 아무 답도 못 받는 상황을 만들지 않기 위해서다.

    같은 도구를 반복해서 부르지 말라는 규율은 프롬프트가 담당하고, 무한 루프는
    여기 max_turns가 막는다. 둘 다 필요하다. 프롬프트만으로는 보장이 안 되고,
    코드만으로는 모델이 왜 멈춰야 하는지 모른다.
    """

    if not has_api_key() or not tools:
        return None

    by_name = {tool.name: tool for tool in tools}
    specs = [tool.to_spec() for tool in tools]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    deadline = time.monotonic() + AGENT_BUDGET_SECONDS
    client = get_client()

    for _ in range(max_turns):
        if time.monotonic() >= deadline:
            break

        try:
            response = await client.chat.completions.create(
                model=EXTRACT_MODEL,
                messages=messages,
                tools=specs,
            )
        except Exception:
            return None

        message = response.choices[0].message
        calls = list(message.tool_calls or [])
        if not calls:
            # 도구를 더 부르지 않았다. 결론을 냈다는 뜻이므로 검증으로 넘어간다.
            break

        # provider가 돌려준 객체를 그대로 넣지 않고 필요한 필드만 다시 만든다.
        # SDK가 붙이는 부가 필드를 provider가 되받지 못하는 경우가 있다.
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in calls
                ],
            }
        )

        # 독립적인 조회는 직렬로 돌리지 않는다. 시간 예산이 빠듯하다.
        results = await asyncio.gather(
            *(
                _dispatch(by_name, call.function.name, call.function.arguments)
                for call in calls
            )
        )
        for call, result in zip(calls, results):
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": result}
            )

    return await _conclude(client, messages, schema)
