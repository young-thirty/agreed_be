"""요구사항 조회와 사람 조작 상태 전이."""

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import get_current_user
from app.public_data import public_requirement
from app.response import fail, ok
from core.domain import Decision, RequirementStatus
from core.state_machine import TRANSITIONS, transition
from models import Requirement
from models.user import User

router = APIRouter(tags=["requirements"])


class TransitionRequest(BaseModel):
    to: RequirementStatus
    # 금액·납기는 사람이 확정한다. 상태를 '제안'이나 '합의'로 올릴 때 함께 받는다.
    decision: Decision | None = None


@router.get("/requirements")
async def list_requirements(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    requirements = await Requirement.find(
        Requirement.ownerId == current_user.id, Requirement.projectId == None
    ).to_list()
    return ok([public_requirement(item) for item in requirements])


@router.get("/requirements/{requirement_id}/allowed")
async def allowed_transitions(
    requirement_id: PydanticObjectId,
    current_user: User | None = Depends(get_current_user),
):
    """화면이 고를 수 있는 상태만 보여주게 하려고 둔다."""
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    requirement = await Requirement.find_one(
        Requirement.id == requirement_id,
        Requirement.ownerId == current_user.id,
        Requirement.projectId == None,
    )
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    return ok({"allowed": list(TRANSITIONS[requirement.status])})


@router.post("/requirements/{requirement_id}/transition")
async def change_status(
    requirement_id: PydanticObjectId,
    body: TransitionRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    requirement = await Requirement.find_one(
        Requirement.id == requirement_id,
        Requirement.ownerId == current_user.id,
        Requirement.projectId == None,
    )
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)

    try:
        requirement.status = transition(requirement.status, body.to)
    except ValueError as error:
        return fail(str(error))

    if body.decision is not None:
        requirement.decision = body.decision

    await requirement.save()
    return ok(public_requirement(requirement))
