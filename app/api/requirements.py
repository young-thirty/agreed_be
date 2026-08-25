"""요구사항 조회와 사람 조작 상태 전이."""

from beanie import PydanticObjectId
from fastapi import APIRouter
from pydantic import BaseModel

from app.response import fail, ok
from core.domain import Decision, RequirementStatus
from core.state_machine import TRANSITIONS, transition
from models import Requirement

router = APIRouter(tags=["requirements"])


class TransitionRequest(BaseModel):
    to: RequirementStatus
    # 금액·납기는 사람이 확정한다. 상태를 '제안'이나 '합의'로 올릴 때 함께 받는다.
    decision: Decision | None = None


@router.get("/requirements")
async def list_requirements():
    return ok(await Requirement.find_all().to_list())


@router.get("/requirements/{requirement_id}/allowed")
async def allowed_transitions(requirement_id: PydanticObjectId):
    """화면이 고를 수 있는 상태만 보여주게 하려고 둔다."""
    requirement = await Requirement.get(requirement_id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    return ok({"allowed": list(TRANSITIONS[requirement.status])})


@router.post("/requirements/{requirement_id}/transition")
async def change_status(requirement_id: PydanticObjectId, body: TransitionRequest):
    requirement = await Requirement.get(requirement_id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)

    try:
        requirement.status = transition(requirement.status, body.to)
    except ValueError as error:
        return fail(str(error))

    if body.decision is not None:
        requirement.decision = body.decision

    await requirement.save()
    return ok(requirement)
