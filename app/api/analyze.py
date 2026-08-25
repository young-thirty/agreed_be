"""대화 분석. 도메인 규칙은 없다. ingest → llm으로 넘기고 저장·응답만 한다."""

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.public_data import public_requirement
from app.response import fail, ok
from core.domain import Channel
from infra.ingest.paste import to_utterances
from infra.llm.extract import extract_requirements
from models import Project, Requirement
from models.user import User

router = APIRouter(tags=["analyze"])


class AnalyzeRequest(BaseModel):
    rawText: str = Field(min_length=1, max_length=100_000)
    channel: Channel
    # 프로젝트를 지정하면 그 프로젝트의 요구사항으로 귀속된다. 지정하지 않으면
    # 프로젝트에 속하지 않는 기존 경로 그대로다. FE 전환 동안 둘 다 살려둔다.
    projectId: PydanticObjectId | None = None


@router.post("/analyze")
async def analyze(
    body: AnalyzeRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)

    if body.projectId is not None:
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
            Requirement.projectId == body.projectId,
        ).to_list()
        extracted = await extract_requirements(utterances, existing)
    except Exception:
        return fail("대화 내용을 분석하지 못했습니다. 다시 시도해 주세요.", 500)

    # 같은 제목이면 기존 카드를 갱신하고, 없으면 새로 만든다.
    existing_by_title = {r.title: r for r in existing}
    saved: list[Requirement] = []

    for state in extracted:
        previous = existing_by_title.get(state.title)
        if previous:
            previous.status = state.status
            previous.evidence = state.evidence
            previous.aiProposedDecision = state.aiProposedDecision
            await previous.save()
            saved.append(previous)
        else:
            created = Requirement(
                **state.model_dump(), ownerId=current_user.id, projectId=body.projectId
            )
            await created.insert()
            saved.append(created)

    return ok(
        {
            "utterances": utterances,
            "requirements": [public_requirement(item) for item in saved],
        }
    )
