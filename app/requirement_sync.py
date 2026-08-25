"""3색 판정에서 요구사항 카드를 만드는 경계.

지금까지 두 파이프라인이 서로를 몰랐다. 채널 수집이 만든 ClientRequest(3색
판정)와 사람이 합의를 진행하는 Requirement(9상태)가 별개 문서였고, 요구사항은
붙여넣기 분석에서만 생겼는데 그쪽은 projectId를 채우지 않았다. 그래서
``GET /projects/{id}/requirements``는 늘 비어 있었고 계약 반영에 입력이 없었다.

여기서 둘을 잇는다. 다만 이 연결은 AI가 하지 않는다. "계약 밖 변경 근거가
분명한 요청은 사람이 판단할 요구사항이 된다"는 것은 규칙이지 추론이 아니다.
CLAUDE.md 6.1이 정한 대로 규칙은 코드가 처리한다.
"""

from beanie import PydanticObjectId

from core.domain import Evidence
from models.client_request import ClientRequest
from models.requirement import Requirement

# 요구사항으로 올릴 판정. 빨강만 올린다.
#
# 주황(확인 필요)까지 올리면 화면이 아직 판단할 거리가 아닌 카드로 덮인다.
# 초록은 계약 안에서 처리하면 되는 일이라 계약 변경 대상이 아니다.
PROMOTED_DECISION = "EXTRA_REQUEST"

# 새로 만든 요구사항이 시작하는 상태.
#
# '미확정'이 아니라 '요청'인 이유는, 여기까지 온 요청은 클라이언트가 명확히
# 요청했고 계약 밖이라는 근거까지 확인된 것이기 때문이다. 상태 전이표상
# '요청'에서 내부검토·제안·거절로 갈 수 있어 사람이 이어서 판단할 수 있다.
INITIAL_STATUS = "요청"


async def sync_requirements_from_requests(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
) -> list[Requirement]:
    """빨강 판정을 받은 요청마다 요구사항 카드를 하나씩 보장한다.

    이미 만들어진 카드는 건드리지 않는다. 사람이 '제안'이나 '합의'까지 올려둔
    카드를 재분석이 '요청'으로 되돌리면 안 된다. sourceRequestId가 멱등 키다.
    """

    requests = await ClientRequest.find(
        ClientRequest.ownerId == owner_id,
        ClientRequest.projectId == project_id,
        ClientRequest.aiDecisionStatus == PROMOTED_DECISION,
    ).to_list()
    if not requests:
        return []

    existing = await Requirement.find(
        Requirement.ownerId == owner_id,
        Requirement.projectId == project_id,
    ).to_list()
    known = {item.sourceRequestId for item in existing if item.sourceRequestId}

    created: list[Requirement] = []
    for request in requests:
        if request.id in known:
            continue

        # 원문 메시지 한 건이 곧 발화 하나다. 붙여넣기 경로처럼 여러 줄로
        # 쪼개지 않았으므로 인덱스는 0이다.
        evidence = [
            Evidence(utteranceIndex=0, quote=item.quote)
            for item in request.requestEvidence
            if item.quote.strip()
        ]

        requirement = Requirement(
            title=(request.summaryTitle or "제목 없는 요청")[:80],
            status=INITIAL_STATUS,
            evidence=evidence,
            # 계약 밖이라 판정해서 여기까지 온 것이므로 계약 근거는 없음이다.
            # 금액·납기는 사람이 정하므로 AI 제안도 비워 둔다.
            basis={"kind": "없음"},
            aiProposedDecision=None,
            decision=None,
            ownerId=owner_id,
            projectId=project_id,
            sourceRequestId=request.id,
        )
        await requirement.insert()
        created.append(requirement)

    return created
