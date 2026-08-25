from beanie import Document, PydanticObjectId
from pymongo import ASCENDING, IndexModel

from core.domain import RequirementState


class Requirement(RequirementState, Document):
    """대화에서 추출한 요구사항.

    RequirementState를 상속하므로 필드를 다시 정의하지 않고, core/의 함수에
    그대로 넘길 수 있다. Beanie를 아는 쪽은 이 파일뿐이다.
    """

    ownerId: PydanticObjectId
    projectId: PydanticObjectId | None = None

    class Settings:
        name = "requirements"
        indexes = [
            IndexModel([("ownerId", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("status", ASCENDING)]),
        ]
