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
    # 이 요구사항을 만든 ClientRequest. 같은 요청으로 카드를 두 번 만들지 않기
    # 위한 멱등 키이자, 화면에서 요구사항과 원본 요청을 잇는 고리다.
    sourceRequestId: PydanticObjectId | None = None

    class Settings:
        name = "requirements"
        indexes = [
            IndexModel([("ownerId", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("projectId", ASCENDING), ("status", ASCENDING)]),
            IndexModel([("ownerId", ASCENDING), ("sourceRequestId", ASCENDING)]),
        ]
