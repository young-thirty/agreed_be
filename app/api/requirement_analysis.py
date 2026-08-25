"""요구사항(Requirement) 단위 분석: 확인 질문 · 답변 초안 · 상태 확정.

app/api/projects.py와 파일을 나눈 이유가 있다. 그 파일에는 요청(ClientRequest)
단위 분석 API도 함께 있는데, 이름이 겹치는 사고가 실제로 두 번 났다
(ReplyDraftRequest, ReplyDraftResult). 같은 파일에 같은 이름의 클래스를 두 번
정의하면 파이썬이 뒤 정의로 조용히 덮어써서, import 에러도 경고도 없이
엔드포인트가 다른 스키마로 검증된다. 클래스가 서로 다른 모듈에 있으면 이름이
같아도 충돌하지 않는다 — 그래서 이 파일을 별도로 둔다.
"""

import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.projects import _project_or_404
from app.auth import get_current_user
from app.public_data import public_requirement
from app.response import fail, ok
from core.domain import Decision, RequirementStatus, Tone, status_change
from core.state_machine import TRANSITIONS, transition
from infra.llm.client import has_api_key
from infra.llm.prompts import build_requirement_text
from infra.llm.reply import build_questions, build_reply
from models import Contract, Project, Requirement
from models.user import User

# 같은 태그("projects")를 쓴다. Swagger에서 프로젝트 화면과 같은 묶음으로
# 보여야 자연스럽고, 새 태그를 만들면 app/openapi.py의 설명도 함께 늘려야 한다.
router = APIRouter(tags=["projects"])

# 사용자에게는 읽을 수 있는 한 문장만 보낸다. 원인은 서버 로그에 남겨야
# 다음에 같은 실패가 났을 때 무엇이 터졌는지 알 수 있다.
logger = logging.getLogger(__name__)


class RequirementTransitionRequest(BaseModel):
    to: RequirementStatus
    decision: Decision | None = None


class RequirementReplyDraftRequest(BaseModel):
    tone: Tone = "professional"
    # 사람이 이 요구사항을 어떤 상태로 확정할지 정한 값. 초안 내용이 여기서 갈린다.
    # 아직 안 정했으면 None이고, 그때는 확인 후 회신하겠다는 중립적인 답이 나온다.
    intent: RequirementStatus | None = None
    # 사람이 채운 금액·납기. 있으면 초안이 [금액]·[기한] 자리를 이 값으로 채운다.
    decision: Decision | None = None
    # 사람이 고르고 고친 질문이 들어온다. 화면에서 전부 뺐으면 빈 목록이다.
    questions: list[str] = Field(default_factory=list, max_length=10)


async def _project_requirement(project: Project, requirement_id: PydanticObjectId, owner_id: PydanticObjectId):
    return await Requirement.find_one(
        Requirement.id == requirement_id, Requirement.ownerId == owner_id,
        Requirement.projectId == project.id,
    )


async def _requirement_text(project: Project, requirement: Requirement, owner_id: PydanticObjectId) -> str:
    """확인 질문과 답변 초안이 함께 보는 재료를 만든다."""
    contract = await Contract.find(
        Contract.ownerId == owner_id, Contract.projectId == project.id
    ).sort(-Contract.version).first_or_none()
    return build_requirement_text(
        project_name=project.name, client_name=project.clientName, contract=contract,
        title=requirement.title, status=requirement.status,
        quotes=[item.quote for item in requirement.evidence],
    )


@router.get("/projects/{project_id}/requirements")
async def project_requirements(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirements = await Requirement.find(
        Requirement.ownerId == current_user.id, Requirement.projectId == project.id
    ).to_list()
    return ok([public_requirement(item) for item in requirements])


@router.post("/projects/{project_id}/requirements/{requirement_id}/questions")
async def requirement_questions(
    project_id: PydanticObjectId, requirement_id: PydanticObjectId,
    current_user: User | None = Depends(get_current_user),
):
    """답변 전에 클라이언트에게 되물을 확인 질문. 고르고 고치는 건 사람이 한다."""
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirement = await _project_requirement(project, requirement_id, current_user.id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    if not has_api_key():
        return fail("AI 설정이 없어 확인 질문을 만들지 못했습니다. 서버 환경변수를 확인해 주세요.", 503)
    try:
        questions = await build_questions(await _requirement_text(project, requirement, current_user.id))
    except Exception:
        logger.exception("확인 질문 생성 실패 (requirement=%s)", requirement_id)
        return fail("확인 질문을 만들지 못했습니다. 다시 시도해 주세요.", 502)
    return ok({"questions": questions})


@router.post("/projects/{project_id}/requirements/{requirement_id}/reply")
async def requirement_reply(
    project_id: PydanticObjectId, requirement_id: PydanticObjectId, body: RequirementReplyDraftRequest,
    current_user: User | None = Depends(get_current_user),
):
    """고객에게 보낼 답변 초안. 보내지는 않는다. 사람이 읽고 고쳐서 직접 보낸다."""
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirement = await _project_requirement(project, requirement_id, current_user.id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    if not has_api_key():
        return fail("AI 설정이 없어 답변 초안을 만들지 못했습니다. 서버 환경변수를 확인해 주세요.", 503)
    try:
        draft = await build_reply(
            await _requirement_text(project, requirement, current_user.id),
            tone=body.tone, questions=body.questions, intent=body.intent,
            decision=body.decision,
        )
    except Exception:
        logger.exception("답변 초안 생성 실패 (requirement=%s)", requirement_id)
        return fail("답변 초안을 만들지 못했습니다. 다시 시도해 주세요.", 502)
    return ok({"draft": draft})


@router.get("/projects/{project_id}/requirements/{requirement_id}/allowed")
async def allowed_project_requirement(
    project_id: PydanticObjectId, requirement_id: PydanticObjectId,
    current_user: User | None = Depends(get_current_user),
):
    """화면이 고를 수 있는 상태만 보여주게 하려고 둔다."""
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirement = await _project_requirement(project, requirement_id, current_user.id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    return ok({"allowed": list(TRANSITIONS[requirement.status])})


@router.post("/projects/{project_id}/requirements/{requirement_id}/transition")
async def transition_project_requirement(
    project_id: PydanticObjectId, requirement_id: PydanticObjectId, body: RequirementTransitionRequest,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirement = await _project_requirement(project, requirement_id, current_user.id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    try:
        next_status = transition(requirement.status, body.to)
    except ValueError as exc:
        return fail(str(exc))
    # 사람이 확정한 변화다. 타임라인에서 AI가 옮긴 것과 구분해 그린다.
    requirement.history = [
        *requirement.history,
        status_change(requirement.status, next_status, by_human=True),
    ]
    requirement.status = next_status
    if body.decision is not None:
        requirement.decision = body.decision
    await requirement.save()
    return ok(public_requirement(requirement))
