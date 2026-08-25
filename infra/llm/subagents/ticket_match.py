"""인바운드 요청을 기존 티켓에 붙일지 판단한다.

임베딩과 벡터 DB를 두지 않는다. 한 프로젝트의 열린 티켓은 현실적으로 수십 개를
넘지 않고, 제목과 요약만 넣으면 20개라도 컨텍스트가 1천 토큰 안쪽이다. 인덱스를
만들고 갱신하는 비용이 이득보다 크다.

본문을 넣지 않는 것도 같은 이유다. "이 요청이 저 티켓과 같은 얘기인가"는 제목
수준에서 대부분 갈린다.
"""

from beanie import PydanticObjectId

from infra.llm.harness import run_json
from infra.llm.prompts import TICKET_MATCH_SYSTEM_PROMPT
from infra.llm.schemas import TicketMatchResult
from models.client_request import ClientRequest

# 컨텍스트에 넣을 열린 티켓 수의 상한. 넘으면 최근 것만 본다.
# 오래된 티켓과 새 요청이 같은 건인 경우는 드물고, 놓쳐도 티켓이 하나 더 생길 뿐
# 데이터가 깨지지 않는다.
MAX_OPEN_TICKETS = 20

_MAX_SUMMARY_CHARS = 80


async def match_open_tickets(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    request_titles: list[str],
) -> list[str | None]:
    """요청마다 붙일 티켓 id를 돌려준다. 새 티켓이면 None이다.

    요청 전부를 한 번의 호출로 판단한다. 요청마다 따로 부르면 같은 티켓 목록을
    여러 번 보내게 되어 토큰만 늘어난다.
    """

    if not request_titles:
        return []

    tickets = (
        await ClientRequest.find(
            ClientRequest.ownerId == owner_id,
            ClientRequest.projectId == project_id,
            ClientRequest.ticketStatus == "active",
        )
        .sort(-ClientRequest.occurredAt)
        .limit(MAX_OPEN_TICKETS)
        .to_list()
    )
    if not tickets:
        return [None] * len(request_titles)

    allowed = {str(ticket.id) for ticket in tickets}
    ticket_lines = "\n".join(
        f"- id={ticket.id} | {(ticket.summaryTitle or '제목 없음')[:_MAX_SUMMARY_CHARS]}"
        for ticket in tickets
    )
    request_lines = "\n".join(
        f"[{index}] {title}" for index, title in enumerate(request_titles)
    )
    task = (
        f"## 열려 있는 티켓\n{ticket_lines}\n\n"
        f"## 새로 들어온 요청\n{request_lines}"
    )

    result = await run_json(
        system_prompt=TICKET_MATCH_SYSTEM_PROMPT,
        user_content=task,
        schema=TicketMatchResult,
    )
    if result is None:
        return [None] * len(request_titles)

    matched: list[str | None] = [None] * len(request_titles)
    for item in result.matches:
        # 목록에 없는 id를 만들어냈으면 새 티켓으로 떨어뜨린다.
        if item.ticketId in allowed and 0 <= item.requestIndex < len(request_titles):
            matched[item.requestIndex] = item.ticketId
    return matched
