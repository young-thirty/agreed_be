"""대화 분석. 도메인 규칙은 없다. ingest → llm으로 넘기고 저장·응답만 한다."""

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.public_data import public_requirement
from app.response import fail, ok
from core.domain import Channel, status_change
from infra.ingest.paste import to_utterances
from infra.llm.extract import extract_requirements
from infra.llm.prompts import build_context_text
from models import Contract, Project, Requirement
from models.user import User

router = APIRouter(tags=["analyze"])


class AnalyzeRequest(BaseModel):
    # 프로젝트 없이는 분석하지 않는다. 누가 클라이언트인지와 계약 내용을 모르면
    # 모델이 멀쩡한 요구사항도 통째로 놓친다.
    projectId: PydanticObjectId
    rawText: str = Field(min_length=1, max_length=100_000)
    channel: Channel


@router.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    project = await Project.find_one(
        Project.id == body.projectId, Project.ownerId == current_user.id
    )
    if project is None:
        return fail("프로젝트를 찾을 수 없습니다.", 404)

    utterances = to_utterances(body.rawText, body.channel)
    if not utterances:
        return fail("대화 내용을 찾을 수 없습니다. 붙여넣은 내용을 확인해 주세요.")

    try:
        existing = await Requirement.find(
            Requirement.ownerId == current_user.id,
            Requirement.projectId == project.id,
        ).to_list()
        contract = (
            await Contract.find(
                Contract.ownerId == current_user.id,
                Contract.projectId == project.id,
            )
            .sort(-Contract.version)
            .first_or_none()
        )
        context = build_context_text(
            project_name=project.name,
            client_name=project.clientName,
            freelancer_name=current_user.name,
            start_date=project.startDate.isoformat() if project.startDate else None,
            end_date=project.endDate.isoformat() if project.endDate else None,
            contract=contract,
            existing=[(str(item.id), item.status, item.title) for item in existing],
        )
        extracted = await extract_requirements(utterances, existing, context)
    except Exception:
        return fail("대화 내용을 분석하지 못했습니다. 다시 시도해 주세요.", 500)

    # 모델이 기존 카드를 가리켰으면 그 카드를, 아니면 같은 제목의 카드를 갱신한다.
    existing_by_id = {str(item.id): item for item in existing}
    existing_by_title = {item.title: item for item in existing}
    saved: list[Requirement] = []

    for matched_id, state in extracted:
        previous = (
            existing_by_id.get(matched_id)
            if matched_id is not None
            else existing_by_title.get(state.title)
        )
        if previous:
            if previous.status != state.status:
                previous.history = [
                    *previous.history,
                    status_change(previous.status, state.status, by_human=False),
                ]
            previous.status = state.status
            previous.evidence = state.evidence
            previous.aiProposedDecision = state.aiProposedDecision
            await previous.save()
            saved.append(previous)
        else:
            created = Requirement(
                **state.model_dump(exclude={"history"}),
                ownerId=current_user.id,
                projectId=project.id,
                history=[status_change(None, state.status, by_human=False)],
            )
            await created.insert()
            saved.append(created)

    return ok(
        {
            "utterances": utterances,
            "requirements": [public_requirement(item) for item in saved],
        }
    )
