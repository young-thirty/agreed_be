"""계약 조회·등록과 L4 승인 게이트.

계약을 변경하는 통로는 /contract/apply 하나뿐이고, 그 안의
core.contract_ops.apply_to_contract가 합의 여부를 검사한다.
"""

from beanie import PydanticObjectId
from fastapi import APIRouter
from pydantic import BaseModel

from app.response import fail, ok
from core.contract_ops import apply_to_contract, diff_contract
from core.domain import ContractState
from models import Contract, Requirement

router = APIRouter(tags=["contract"])


class ApplyRequest(BaseModel):
    requirementId: PydanticObjectId


async def _current_contract() -> Contract | None:
    """계약은 버전이 올라갈 때마다 새 문서가 쌓인다. 최신 버전이 현재 계약이다."""
    return await Contract.find_all().sort(-Contract.version).first_or_none()


@router.get("/contract")
async def get_contract():
    contract = await _current_contract()
    if contract is None:
        return fail("등록된 계약이 없습니다. 최초 계약을 먼저 등록해 주세요.", 404)
    return ok(contract)


@router.post("/contract")
async def create_contract(body: ContractState):
    """최초 계약 등록. 시연 세팅용이다."""
    contract = Contract(**body.model_dump())
    await contract.insert()
    return ok(contract)


@router.post("/contract/apply")
async def apply(body: ApplyRequest):
    contract = await _current_contract()
    if contract is None:
        return fail("등록된 계약이 없습니다. 최초 계약을 먼저 등록해 주세요.", 404)

    requirement = await Requirement.get(body.requirementId)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)

    try:
        next_state = apply_to_contract(contract, requirement)
    except ValueError as error:
        return fail(str(error))

    next_contract = Contract(**next_state.model_dump())
    await next_contract.insert()

    return ok(
        {
            "contract": next_contract,
            "diff": diff_contract(contract, next_state),
        }
    )
