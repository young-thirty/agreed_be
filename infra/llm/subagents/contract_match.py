"""계약 대조 서브 에이전트.

클라이언트 요청 한 건이 현재 계약·자료의 범위 안에 있는지 판정한다. 이전에는
라우트가 ``contract.scope`` 문자열이 원문에 그대로 들어있는지 보는 substring
매칭이었다. 계약 조항과 요청 문장의 표현이 다르면 아무것도 못 찾는 방식이라,
도구를 쥔 서브 에이전트로 바꾼다.

모델에게 주는 도구는 읽기 전용 두 개뿐이다. 계약을 바꾸거나 메일을 보내는 도구는
주지 않는다. 소유권 조건은 도구를 만들 때 클로저로 묶으므로 모델이 다른 사용자의
자료를 지정할 방법이 없다.
"""

from beanie import PydanticObjectId

from core.grounding import is_quote_in
from core.project_data import AiDecisionStatus
from infra.llm.harness import AgentTool, run_agent
from infra.llm.prompts import CONTRACT_MATCH_SYSTEM_PROMPT
from infra.llm.schemas import ContractMatchResult
from models.contract import Contract
from models.project_material import ProjectMaterial

# 근거를 찾지 못했을 때 내려가는 자리. 초록·빨강을 억지로 고르지 않는다.
FALLBACK_DECISION: AiDecisionStatus = "OUT_OF_SCOPE_COORDINATION_REQUIRED"

# 자료 검색이 한 번에 돌려주는 문서 수와 발췌 길이.
_MAX_MATERIALS = 3
_SNIPPET_CHARS = 600


async def _current_contract(
    owner_id: PydanticObjectId, project_id: PydanticObjectId
) -> Contract | None:
    return (
        await Contract.find(Contract.ownerId == owner_id, Contract.projectId == project_id)
        .sort(-Contract.version)
        .first_or_none()
    )


def _contract_text(contract: Contract) -> str:
    lines = [f"계약 {contract.version}버전", f"납기: {contract.dueDate}", f"금액: {contract.amount}"]
    lines.extend(f"- {item}" for item in contract.scope)
    return "\n".join(lines)


def _build_tools(
    owner_id: PydanticObjectId, project_id: PydanticObjectId
) -> tuple[list[AgentTool], dict[str, str]]:
    """도구와, 도구가 내보인 문서 원문 모음을 함께 만든다.

    원문 모음은 나중에 모델이 옮겨 적은 인용을 코드가 다시 대조할 때 쓴다.
    도구가 보여주지 않은 문서를 근거로 댈 수는 없어야 한다.
    """

    shown: dict[str, str] = {}

    async def read_contract(_: dict) -> str:
        contract = await _current_contract(owner_id, project_id)
        if contract is None:
            return "이 프로젝트에는 등록된 계약이 없습니다."
        text = _contract_text(contract)
        shown[str(contract.id)] = text
        return f"documentId={contract.id}\n{text}"

    async def search_materials(arguments: dict) -> str:
        keyword = str(arguments.get("keyword", "")).strip()
        materials = (
            await ProjectMaterial.find(
                ProjectMaterial.ownerId == owner_id,
                ProjectMaterial.projectId == project_id,
            )
            .sort(-ProjectMaterial.communicatedAt)
            .to_list()
        )
        if not materials:
            return "이 프로젝트에 등록된 자료가 없습니다."

        matched = [
            material
            for material in materials
            if not keyword
            or keyword in material.fileName
            or (material.extractedText and keyword in material.extractedText)
        ][:_MAX_MATERIALS]
        if not matched:
            return f"'{keyword}'와 관련된 자료를 찾지 못했습니다."

        blocks: list[str] = []
        for material in matched:
            body = (material.extractedText or "").strip()
            if body:
                shown[str(material.id)] = body
            blocks.append(
                f"documentId={material.id}\n"
                f"파일명: {material.fileName}\n"
                f"종류: {material.documentType or '미분류'}\n"
                f"본문: {body[:_SNIPPET_CHARS] if body else '(추출된 텍스트 없음)'}"
            )
        return "\n\n".join(blocks)

    tools = [
        AgentTool(
            name="read_contract",
            description="이 프로젝트의 현재 계약(최신 버전)의 범위·납기·금액을 읽는다. 인자는 없다.",
            parameters={"type": "object", "properties": {}},
            run=read_contract,
        ),
        AgentTool(
            name="search_materials",
            description=(
                "이 프로젝트에 등록된 자료(제안서·계약서·요구사항 문서·회의록)를 "
                "키워드로 찾아 파일명과 본문 발췌를 돌려준다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "찾을 키워드. 요청에서 핵심이 되는 단어 하나를 쓴다.",
                    }
                },
                "required": ["keyword"],
            },
            run=search_materials,
        ),
    ]
    return tools, shown


def _verify(result: ContractMatchResult, shown: dict[str, str]) -> ContractMatchResult:
    """모델이 옮겨 적은 근거를 코드가 다시 대조한다. L2 규칙을 문서에 적용한 것이다.

    도구가 보여준 적 없는 문서를 대거나, 인용문이 그 문서에 실제로 없으면 근거를
    버린다. 근거가 사라졌는데 판정이 초록이었다면 주황으로 내린다. 빨강은 그대로
    둔다. 계약 밖 변경이라는 판단은 계약에 없다는 사실 자체가 근거이기 때문이다.
    """

    source = shown.get(result.documentId, "")
    if source and is_quote_in(source, result.documentQuote):
        return result

    decision = result.decision
    if decision == "IN_SCOPE_ACTION_REQUIRED":
        decision = FALLBACK_DECISION
    return ContractMatchResult(
        decision=decision,
        reason=result.reason,
        documentQuote="",
        documentId="",
    )


async def match_against_contract(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    summary_title: str,
    request_quote: str,
    raw_text: str,
) -> ContractMatchResult:
    """요청 한 건을 계약·자료와 대조해 3색 판정을 돌려준다.

    모델을 부르지 못했거나 결론이 검증을 통과하지 못하면 주황으로 내려보낸다.
    예외를 위로 던지지 않는다. 판정 하나가 실패해도 나머지 요청은 살아야 한다.
    """

    tools, shown = _build_tools(owner_id, project_id)
    task = (
        f"요청 요약: {summary_title}\n"
        f"요청 근거 문장: {request_quote or '(없음)'}\n\n"
        f"클라이언트 원문:\n{raw_text[:4000]}"
    )

    try:
        result = await run_agent(
            system_prompt=CONTRACT_MATCH_SYSTEM_PROMPT,
            task=task,
            tools=tools,
            schema=ContractMatchResult,
        )
    except Exception:
        result = None

    if result is None:
        return ContractMatchResult(
            decision=FALLBACK_DECISION,
            reason="확인 가능한 근거가 부족합니다.",
        )
    return _verify(result, shown)
