"""티켓 솔루션 패키지 중 AI가 만드는 부분.

주의: 솔루션 생성 경로는 infra/llm/solution.py의 build_solution으로 옮겨갔다.
이 모듈은 현재 호출되지 않는다. 지우지 않고 남겨 둔 것은 조언 생성만 단독으로
쓸 자리가 생길 수 있어서다. 새 코드는 build_solution을 쓴다.

조언 메시지와 이유, 그리고 근거 조문을 만든다. 관련 파일 목록은 여기서 만들지
않는다 — 어떤 자료가 이 프로젝트에 있는지는 DB가 아는 사실이라 추론할 대상이
아니기 때문이다. 호출부가 조회해서 붙인다.

계약 대조 서브 에이전트(contract_match.py)가 이미 3색 판정을 냈다. 이 파일은 그
판정을 사람이 읽을 문장으로 바꾸는 층이다. 판정을 다시 하지 않는다.
"""

from beanie import PydanticObjectId

from core.grounding import is_quote_in
from infra.llm.client import has_api_key
from infra.llm.harness import run_json
from infra.llm.prompts import TICKET_ADVICE_SYSTEM_PROMPT
from infra.llm.schemas import TicketAdviceResult
from models.contract import Contract
from models.project_material import ProjectMaterial

_MAX_MATERIAL_CHARS = 1500
_MAX_MATERIALS = 3


async def _context(
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    ticket_id: PydanticObjectId,
) -> tuple[str, dict[str, str]]:
    """모델에게 보여줄 계약·자료와, 나중에 인용을 대조할 원문 모음을 함께 만든다."""
    shown: dict[str, str] = {}
    blocks: list[str] = []

    contract = (
        await Contract.find(Contract.ownerId == owner_id, Contract.projectId == project_id)
        .sort(-Contract.version)
        .first_or_none()
    )
    if contract is not None:
        text = "\n".join(
            [f"계약 {contract.version}버전", f"납기: {contract.dueDate}", f"금액: {contract.amount}"]
            + [f"- {item}" for item in contract.scope]
        )
        shown[str(contract.id)] = text
        blocks.append(f"documentId={contract.id}\n{text}")

    materials = await ProjectMaterial.find(
        ProjectMaterial.ownerId == owner_id,
        ProjectMaterial.projectId == project_id,
        ProjectMaterial.ticketId == ticket_id,
    ).sort(-ProjectMaterial.communicatedAt).limit(_MAX_MATERIALS).to_list()
    if len(materials) < _MAX_MATERIALS:
        shared_materials = (
            await ProjectMaterial.find(
                ProjectMaterial.ownerId == owner_id,
                ProjectMaterial.projectId == project_id,
                ProjectMaterial.ticketId == None,  # noqa: E711 - MongoDB null 조건
            )
            .sort(-ProjectMaterial.communicatedAt)
            .limit(_MAX_MATERIALS - len(materials))
            .to_list()
        )
        materials.extend(shared_materials)
    for material in materials:
        body = (material.extractedText or "").strip()
        if not body:
            continue
        shown[str(material.id)] = body
        blocks.append(
            f"documentId={material.id}\n파일명: {material.fileName}\n본문: {body[:_MAX_MATERIAL_CHARS]}"
        )

    return ("\n\n".join(blocks) or "(등록된 계약·자료가 없습니다)"), shown


def _no_advice() -> TicketAdviceResult:
    """조언을 만들지 못했을 때. 화면이 비지 않도록 안내 문장을 돌려준다."""
    return TicketAdviceResult(
        adviceMessage="자동 조언을 만들지 못했습니다. 원문과 계약을 직접 확인해 주세요.",
        adviceReason="",
    )


async def build_ticket_advice(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    ticket_id: PydanticObjectId,
    summary_title: str,
    decision: str,
    request_quote: str,
) -> TicketAdviceResult:
    """조언과 근거를 만든다. 실패하거나 인용이 허구면 근거 없이 돌려준다."""

    if not has_api_key():
        # 키가 없으면 계약·자료를 조회할 이유가 없다. DB를 헛돌지 않는다.
        return _no_advice()

    documents, shown = await _context(owner_id, project_id, ticket_id)
    task = (
        f"## 클라이언트 요청\n{summary_title}\n"
        f"원문 인용: {request_quote or '(없음)'}\n\n"
        f"## 이 요청에 대한 범위 판정\n{decision}\n\n"
        f"## 계약과 자료\n{documents}"
    )

    result = await run_json(
        system_prompt=TICKET_ADVICE_SYSTEM_PROMPT,
        user_content=task,
        schema=TicketAdviceResult,
    )
    if result is None:
        return _no_advice()

    # L2를 문서에 적용한다. 지어낸 인용이면 근거만 버리고 조언은 살린다.
    source = shown.get(result.basisDocumentId, "")
    if not (source and is_quote_in(source, result.basisQuote)):
        return TicketAdviceResult(
            adviceMessage=result.adviceMessage,
            adviceReason=result.adviceReason,
        )
    return result
