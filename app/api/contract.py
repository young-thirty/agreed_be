"""계약 조회·등록과 L4 승인 게이트.

계약을 변경하는 통로는 /contract/apply 하나뿐이고, 그 안의
core.contract_ops.apply_to_contract가 합의 여부를 검사한다.
"""

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from pymongo.errors import DuplicateKeyError

from app.auth import get_current_user
from app.public_data import public_contract
from app.response import fail, ok
from core.contract_ops import apply_to_contract, diff_contract
from core.domain import ContractState
from models import Contract, Requirement
from models.user import User

router = APIRouter(tags=["contract"])


class ApplyRequest(BaseModel):
    requirementId: PydanticObjectId


async def _current_contract(owner_id: PydanticObjectId) -> Contract | None:
    """계약은 버전이 올라갈 때마다 새 문서가 쌓인다. 최신 버전이 현재 계약이다."""
    return (
        await Contract.find(Contract.ownerId == owner_id, Contract.projectId == None)
        .sort(-Contract.version)
        .first_or_none()
    )


async def _applied_contract(
    owner_id: PydanticObjectId,
    requirement_id: PydanticObjectId,
) -> Contract | None:
    return await Contract.find_one(
        Contract.ownerId == owner_id,
        Contract.projectId == None,
        Contract.appliedRequirementId == str(requirement_id),
    )


async def _applied_result(owner_id: PydanticObjectId, contract: Contract):
    previous = await Contract.find_one(
        Contract.ownerId == owner_id,
        Contract.projectId == None,
        Contract.version == contract.version - 1,
    )
    if previous is None:
        return fail("계약 반영 이력을 확인하지 못했습니다.", 500)
    return ok(
        {"contract": public_contract(contract), "diff": diff_contract(previous, contract)}
    )


@router.get("/contract")
async def get_contract(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    contract = await _current_contract(current_user.id)
    if contract is None:
        return fail("등록된 계약이 없습니다. 최초 계약을 먼저 등록해 주세요.", 404)
    return ok(public_contract(contract))


@router.post("/contract")
async def create_contract(
    body: ContractState,
    current_user: User | None = Depends(get_current_user),
):
    """최초 계약 등록. 시연 세팅용이다."""
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    if await _current_contract(current_user.id) is not None:
        return fail("이미 등록된 계약이 있습니다.", 409)
    if body.version != 1 or body.appliedRequirementId is not None:
        return fail("최초 계약은 1버전으로 등록해 주세요.")

    contract = Contract(**body.model_dump(), ownerId=current_user.id)
    try:
        await contract.insert()
    except DuplicateKeyError:
        return fail("이미 등록된 계약이 있습니다.", 409)
    return ok(public_contract(contract))


@router.post("/contract/apply")
async def apply(
    body: ApplyRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    requirement = await Requirement.find_one(
        Requirement.id == body.requirementId,
        Requirement.ownerId == current_user.id,
    )
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)

    applied = await _applied_contract(current_user.id, body.requirementId)
    if applied is not None:
        return await _applied_result(current_user.id, applied)

    contract = await _current_contract(current_user.id)
    if contract is None:
        return fail("등록된 계약이 없습니다. 최초 계약을 먼저 등록해 주세요.", 404)

    try:
        next_state = apply_to_contract(contract, requirement, str(requirement.id))
    except ValueError as error:
        return fail(str(error))

    next_contract = Contract(**next_state.model_dump(), ownerId=current_user.id)
    try:
        await next_contract.insert()
    except DuplicateKeyError:
        # 같은 요구사항의 동시·재시도는 먼저 성공한 결과를 그대로 돌려준다.
        applied = await _applied_contract(current_user.id, body.requirementId)
        if applied is not None:
            return await _applied_result(current_user.id, applied)
        return fail(
            "계약이 동시에 변경됐습니다. 현재 계약을 확인한 뒤 다시 시도해 주세요.",
            409,
        )

    return ok(
        {
            "contract": public_contract(next_contract),
            "diff": diff_contract(contract, next_state),
        }
    )
