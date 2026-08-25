from beanie import Document

from core.domain import ContractState


class Contract(ContractState, Document):
    """살아있는 계약. 사람이 승인할 때마다 새 버전이 쌓인다.

    ContractState를 상속하므로 필드를 다시 정의하지 않고, core/의 함수에
    그대로 넘길 수 있다. Beanie를 아는 쪽은 이 파일뿐이다.
    """

    class Settings:
        name = "contracts"
