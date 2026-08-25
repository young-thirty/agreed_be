from beanie import Document, PydanticObjectId
from pymongo import ASCENDING, IndexModel

from core.domain import ContractState


class Contract(ContractState, Document):
    """살아있는 계약. 사람이 승인할 때마다 새 버전이 쌓인다.

    ContractState를 상속하므로 필드를 다시 정의하지 않고, core/의 함수에
    그대로 넘길 수 있다. Beanie를 아는 쪽은 이 파일뿐이다.
    """

    ownerId: PydanticObjectId
    projectId: PydanticObjectId | None = None

    class Settings:
        name = "contracts"
        indexes = [
            IndexModel(
                [("ownerId", ASCENDING), ("version", ASCENDING)],
                unique=True,
            ),
            IndexModel(
                [("ownerId", ASCENDING), ("appliedRequirementId", ASCENDING)],
                unique=True,
                partialFilterExpression={"appliedRequirementId": {"$type": "string"}},
            ),
            IndexModel(
                [("ownerId", ASCENDING), ("projectId", ASCENDING), ("version", ASCENDING)],
                unique=True,
                partialFilterExpression={"projectId": {"$type": "objectId"}},
            ),
            IndexModel(
                [("ownerId", ASCENDING), ("projectId", ASCENDING), ("appliedRequirementId", ASCENDING)],
                unique=True,
                partialFilterExpression={"projectId": {"$type": "objectId"}, "appliedRequirementId": {"$type": "string"}},
            ),
        ]
