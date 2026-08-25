"""프로젝트 중심의 수집·요청·자료 API.

시연 MVP는 외부 provider에서 가져온 원문을 서버에 저장하고, FastAPI
BackgroundTasks로 분석한다. 프론트는 provider API를 직접 호출하지 않는다.
"""

import hashlib
import os
from datetime import date, datetime, timezone
from typing import Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.auth import get_current_user
from app.integration_store import (
    access_token, github_connection, latest_gmail_connection, slack_connection,
)
from app.public_data import public_material, public_project
from app.requirement_sync import sync_requirements_from_requests
from app.response import fail, ok
from core.contract_ops import apply_to_contract, diff_contract
from core.domain import ContractState, Decision, RequirementStatus, Tone, status_change
from core.project_data import (
    DocumentType, ProcessingStatus, ProjectSort, ProjectStatus,
    ResponseStatus, SourceChannel,
)
from infra.integrations import IntegrationError
from infra.integrations.gmail import (
    GMAIL_SCOPES, fetch_recent, refresh_access_token,
)
from infra.integrations.slack import fetch_file, fetch_history
from infra.llm.client import EXTRACT_MODEL, has_api_key
from infra.llm.harness import run_json
from infra.llm.orchestrator import AnalyzedRequest, analyze_request_message
from infra.llm.subagents.checklist import build_checklist
from infra.llm.subagents.git_explore import ask_repository
from infra.llm.subagents.reply_draft import build_reply_draft
from infra.llm.prompts import PROJECT_MATERIAL_SYSTEM_PROMPT, build_requirement_text
from infra.llm.reply import build_questions, build_reply
from infra.llm.schemas import MaterialClassificationResult
from infra.security.provider_tokens import TokenEncryptionError
from infra.storage.s3 import has_s3, put_object
from models import (
    AnalysisRun, ClientRequest, Contract, Project, ProjectMaterial,
    ProjectSourceLink, Requirement, SourceMessage,
)
from models.client_request import public_client_request
from models.integration import IntegrationConnection
from models.user import User

# 태그는 라우터가 아니라 경로마다 붙인다. 이 파일 하나가 프로젝트·수집·요청·계약
# 네 묶음을 담고 있어서, 라우터 레벨로 묶으면 Swagger에서 전부 한 덩어리로 보인다.
router = APIRouter()


def _now() -> datetime:
    return datetime.utcnow()


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _status_rank(status: ProjectStatus) -> int:
    return {"ACTIVE": 0, "DRAFT": 1, "COMPLETED": 2}[status]


class ProjectUpdateRequest(BaseModel):
    """사람이 화면에서 고칠 수 있는 값. 폼이 늘 전부 보내므로 부분 갱신은 받지 않는다.

    status는 여기에 없다. 진행 상태는 /status가 따로 맡는다. 수정 폼이
    status를 함께 보내면 안 보낸 경우와 구분하지 못해 진행 중인 프로젝트가
    조용히 Draft로 되돌아갈 수 있다.
    """


    name: str = Field(min_length=1, max_length=120)
    clientName: str = Field(min_length=1, max_length=120)
    clientEmail: str | None = Field(
        default=None,
        max_length=320,
        pattern=r"^[^\s@]+@[^\s@]+$",
    )
    description: str = Field(default="", max_length=1000)
    startDate: date | None = None
    endDate: date | None = None
    contractPrice: int | None = Field(default=None, ge=0)


class ProjectCreateRequest(ProjectUpdateRequest):
    status: ProjectStatus = "DRAFT"


class ProjectStatusRequest(BaseModel):
    status: ProjectStatus


class SourceLinkRequest(BaseModel):
    sourceChannel: SourceChannel
    displayName: str = Field(min_length=1, max_length=160)
    connectionId: str | None = None
    counterpartyEmail: str | None = None
    threadId: str | None = None
    teamId: str | None = None
    channelId: str | None = None
    # GITHUB 전용. "owner/repo" 형식이다.
    repoFullName: str | None = None
    locatorKey: str = Field(min_length=1, max_length=300)


class GitAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class ContractApplyRequest(BaseModel):
    requirementId: PydanticObjectId


class RequirementTransitionRequest(BaseModel):
    to: RequirementStatus
    decision: Decision | None = None


class ReplyDraftRequest(BaseModel):
    tone: Tone = "professional"
    # 사람이 이 요구사항을 어떤 상태로 확정할지 정한 값. 초안 내용이 여기서 갈린다.
    # 아직 안 정했으면 None이고, 그때는 확인 후 회신하겠다는 중립적인 답이 나온다.
    intent: RequirementStatus | None = None
    # 사람이 채운 금액·납기. 있으면 초안이 [금액]·[기한] 자리를 이 값으로 채운다.
    decision: Decision | None = None
    # 사람이 고르고 고친 질문이 들어온다. 화면에서 전부 뺐으면 빈 목록이다.
    questions: list[str] = Field(default_factory=list, max_length=10)


async def get_owned_project(project_id: PydanticObjectId, user: User | None) -> Project | None:
    if user is None:
        return None
    return await Project.find_one(Project.id == project_id, Project.ownerId == user.id)


async def _unanswered_count(project_id: PydanticObjectId, owner_id: PydanticObjectId) -> int:
    return await ClientRequest.find(
        ClientRequest.ownerId == owner_id,
        ClientRequest.projectId == project_id,
        ClientRequest.responseStatus == "WAITING",
    ).count()


async def _project_or_404(project_id: PydanticObjectId, user: User | None):
    if user is None:
        return None, fail("로그인이 필요합니다.", 401)
    project = await get_owned_project(project_id, user)
    if project is None:
        return None, fail("프로젝트를 찾을 수 없습니다.", 404)
    return project, None


@router.post("/projects", tags=["project"])
async def create_project(body: ProjectCreateRequest, current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    project = Project(
        ownerId=current_user.id, name=body.name, clientName=body.clientName,
        clientEmail=body.clientEmail, description=body.description,
        startDate=body.startDate, endDate=body.endDate, contractPrice=body.contractPrice,
        status=body.status, statusRank=_status_rank(body.status),
    )
    await project.insert()
    return ok(public_project(project))


@router.get("/projects", tags=["project"])
async def list_projects(
    status: ProjectStatus | None = None,
    sort: ProjectSort = "status",
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    query = {"ownerId": current_user.id}
    if status:
        query["status"] = status
    projects = await Project.find(query).to_list()
    if sort == "status":
        projects.sort(key=lambda p: (p.statusRank, -p.updatedAt.timestamp()))
    elif sort == "updatedAt":
        projects.sort(key=lambda p: p.updatedAt, reverse=True)
    else:
        projects.sort(key=lambda p: p.createdAt, reverse=True)
    return ok([
        public_project(project, await _unanswered_count(project.id, current_user.id))
        for project in projects
    ])


@router.get("/projects/{project_id}", tags=["project"])
async def project_detail(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    return ok(public_project(project, await _unanswered_count(project.id, current_user.id)))


@router.patch("/projects/{project_id}", tags=["project"])
async def update_project(
    project_id: PydanticObjectId, body: ProjectUpdateRequest,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    project.name = body.name
    project.clientName = body.clientName
    project.clientEmail = body.clientEmail
    project.description = body.description
    project.startDate = body.startDate
    project.endDate = body.endDate
    project.contractPrice = body.contractPrice
    project.updatedAt = _now()
    await project.save()
    return ok(public_project(project, await _unanswered_count(project.id, current_user.id)))


@router.patch("/projects/{project_id}/status", tags=["project"])
async def update_project_status(
    project_id: PydanticObjectId, body: ProjectStatusRequest,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    project.status = body.status
    project.statusRank = _status_rank(body.status)
    project.updatedAt = _now()
    await project.save()
    return ok(public_project(project, await _unanswered_count(project.id, current_user.id)))


@router.get("/projects/{project_id}/requests", tags=["request"])
async def project_requests(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requests = await ClientRequest.find(
        ClientRequest.ownerId == current_user.id, ClientRequest.projectId == project.id
    ).sort(-ClientRequest.occurredAt).to_list()
    return ok([public_client_request(item) for item in requests])


@router.get("/requests/{request_id}", tags=["request"])
async def request_detail(request_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    item = await ClientRequest.find_one(ClientRequest.id == request_id, ClientRequest.ownerId == current_user.id)
    if item is None:
        return fail("요청을 찾을 수 없습니다.", 404)
    message = await SourceMessage.find_one(SourceMessage.id == item.sourceMessageId, SourceMessage.ownerId == current_user.id)
    data = public_client_request(item)
    data.update({
        "requestEvidence": [e.model_dump(mode="json") for e in item.requestEvidence],
        "documentEvidence": [e.model_dump(mode="json") for e in item.documentEvidence],
        # decisionReason은 PRODUCT_API_DESIGN.md의 확정 공개 DTO에는 없지만,
        # 이 endpoint 자체가 이미 그 DTO를 넘어선 상세 화면용이라 함께 둔다.
        "decisionReason": item.decisionReason,
        "sourceText": message.rawText if message else None,
        "conversationDisplay": message.conversationDisplay if message else None,
    })
    return ok(data)


class ReplyDraftRequest(BaseModel):
    selectedItems: list[str] = Field(default_factory=list, max_length=6)
    tone: Literal["friendly", "professional", "concise", "firm"] = "professional"


class ResponseStatusRequest(BaseModel):
    responseStatus: ResponseStatus


async def _owned_request(request_id: PydanticObjectId, owner_id: PydanticObjectId) -> ClientRequest | None:
    return await ClientRequest.find_one(
        ClientRequest.id == request_id, ClientRequest.ownerId == owner_id
    )


@router.post("/requests/{request_id}/checklist", tags=["request"])
async def request_checklist(
    request_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)
):
    """답변 전에 확인할 항목을 만든다. DATA_AI_PIPELINE.md §5 6단계.

    매 sync마다 만들지 않고, 사람이 카드를 열어 실제로 필요할 때만 호출한다.
    """
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    item = await _owned_request(request_id, current_user.id)
    if item is None:
        return fail("요청을 찾을 수 없습니다.", 404)
    items = await build_checklist(
        summary_title=item.summaryTitle or "",
        reason=item.decisionReason or "",
        request_quote=item.requestEvidence[0].quote if item.requestEvidence else "",
    )
    return ok({"items": items})


@router.post("/requests/{request_id}/reply-draft", tags=["request"])
async def request_reply_draft(
    request_id: PydanticObjectId,
    body: ReplyDraftRequest,
    current_user: User | None = Depends(get_current_user),
):
    """고객에게 보낼 답변 초안을 만든다. DATA_AI_PIPELINE.md §5 7단계.

    사람이 체크리스트에서 고른 항목만 반영한다. 생성만 하고 발송하지 않는다 —
    발송 endpoint는 아직 없다(HANDOFF.md 보류 목록).
    """
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    item = await _owned_request(request_id, current_user.id)
    if item is None:
        return fail("요청을 찾을 수 없습니다.", 404)
    selected = [text.strip() for text in body.selectedItems if text.strip()][:6]
    reply = await build_reply_draft(
        summary_title=item.summaryTitle or "", selected_items=selected, tone=body.tone
    )
    return ok({"body": reply})


@router.patch("/requests/{request_id}/response-status", tags=["request"])
async def update_response_status(
    request_id: PydanticObjectId,
    body: ResponseStatusRequest,
    current_user: User | None = Depends(get_current_user),
):
    """사람이 대응 상태를 바꾼다. AI는 관여하지 않는다.

    WAITING에서 나갈 경로가 없어 unansweredRequestCount가 줄어들 방법이
    없었다. 이 경로가 그 유일한 출구다.
    """
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    item = await _owned_request(request_id, current_user.id)
    if item is None:
        return fail("요청을 찾을 수 없습니다.", 404)
    item.responseStatus = body.responseStatus
    item.updatedAt = _now()
    await item.save()
    return ok(public_client_request(item))


@router.get("/projects/{project_id}/materials", tags=["request"])
async def project_materials(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    materials = await ProjectMaterial.find(
        ProjectMaterial.ownerId == current_user.id, ProjectMaterial.projectId == project.id
    ).sort(-ProjectMaterial.communicatedAt).to_list()
    return ok([public_material(item) for item in materials])


@router.get("/projects/{project_id}/source-links", tags=["ingest"])
async def source_links(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    links = await ProjectSourceLink.find(
        ProjectSourceLink.ownerId == current_user.id, ProjectSourceLink.projectId == project.id
    ).sort(ProjectSourceLink.createdAt).to_list()
    return ok([item.model_dump(mode="json", exclude={"id", "ownerId"}) | {"sourceLinkId": str(item.id)} for item in links])


@router.post("/projects/{project_id}/source-links", tags=["ingest"])
async def create_source_link(
    project_id: PydanticObjectId, body: SourceLinkRequest,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    owner_id = str(current_user.id)
    if body.sourceChannel == "GMAIL":
        connection = await latest_gmail_connection(owner_id)
        if connection is None or (body.connectionId and connection.externalId != body.connectionId):
            return fail("연결된 Gmail 계정을 찾을 수 없습니다.", 404)
        body.connectionId = connection.externalId
    elif body.sourceChannel == "SLACK":
        if not body.teamId or not body.channelId:
            return fail("Slack 링크에는 teamId와 channelId가 필요합니다.")
        connection = await slack_connection(owner_id, body.teamId)
        if connection is None:
            return fail("연결된 Slack 워크스페이스를 찾을 수 없습니다.", 404)
        body.connectionId = body.connectionId or connection.externalId
    else:
        # GITHUB. OAuth 연동이 아니라 서버 GITHUB_TOKEN으로 clone하므로
        # provider 연결 확인이 필요 없다. 형식만 검증한다.
        if not body.repoFullName or body.repoFullName.count("/") != 1:
            return fail("레포 이름은 owner/repo 형식이어야 합니다.")
        body.locatorKey = body.repoFullName
    link = ProjectSourceLink(ownerId=current_user.id, projectId=project.id, **body.model_dump())
    try:
        await link.insert()
    except DuplicateKeyError:
        return fail("같은 채널 연결이 이미 등록되어 있습니다.", 409)
    return ok(link.model_dump(mode="json", exclude={"id", "ownerId"}) | {"sourceLinkId": str(link.id)})


async def _gmail_connection_token(connection: IntegrationConnection) -> tuple[IntegrationConnection, str]:
    from app.integration_store import refresh_token, save_gmail_connection, utc_now
    from infra.integrations.gmail import refresh_access_token
    from infra.security.provider_tokens import decrypt_provider_token
    token = decrypt_provider_token(connection.accessTokenEncrypted)
    if connection.accessTokenExpiresAt and connection.accessTokenExpiresAt > utc_now():
        return connection, token
    refresh = refresh_token(connection)
    if not refresh:
        return connection, token
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    refreshed = await refresh_access_token(refresh_token=refresh, client_id=client_id, client_secret=client_secret)
    connection = await save_gmail_connection(
        owner_id=connection.ownerId, email=connection.externalId,
        access_token=refreshed.accessToken, refresh_token=refreshed.refreshToken,
        expires_at=datetime.utcfromtimestamp(refreshed.expiresAt / 1000), scopes=list(GMAIL_SCOPES),
    )
    return connection, access_token(connection)


async def _upsert_source_message(link: ProjectSourceLink, connection_id: str, *, source_key: str,
                                 provider_id: str, provider_thread_id: str | None, sender_id: str | None,
                                 sender_display: str | None, conversation: str | None, direction: Literal["RECEIVED", "SENT"],
                                 raw_text: str, occurred_at: datetime, attachments: list[str]) -> tuple[SourceMessage, bool]:
    existing = await SourceMessage.find_one(
        SourceMessage.ownerId == link.ownerId, SourceMessage.sourceChannel == link.sourceChannel,
        SourceMessage.connectionId == connection_id, SourceMessage.sourceKey == source_key,
    )
    if existing:
        return existing, False
    message = SourceMessage(
        ownerId=link.ownerId, projectId=link.projectId, sourceLinkId=link.id,
        connectionId=connection_id, sourceChannel=link.sourceChannel, sourceKey=source_key,
        providerMessageId=provider_id, providerThreadId=provider_thread_id,
        senderExternalId=sender_id, senderDisplay=sender_display,
        conversationDisplay=conversation, direction=direction, rawText=raw_text,
        occurredAt=occurred_at, contentHash=_hash(raw_text), attachmentRefs=attachments,
    )
    try:
        await message.insert()
        return message, True
    except DuplicateKeyError:
        existing = await SourceMessage.find_one(
            SourceMessage.ownerId == link.ownerId, SourceMessage.sourceChannel == link.sourceChannel,
            SourceMessage.connectionId == connection_id, SourceMessage.sourceKey == source_key,
        )
        if existing:
            return existing, False
        raise


# CLIENT_REQUEST 판정을 오케스트레이터(요청 다건 추출 + 계약 대조 서브 에이전트)로
# 바꾸면서 올린다. AnalysisRun의 unique key가 (ownerId,targetType,inputHash,
# promptVersion)이라, 버전을 그대로 두면 이전 단발 판정 결과가 캐시로 재사용되어
# 새 파이프라인이 아예 안 돈다. MATERIAL_CLASSIFICATION은 로직이 그대로라 "v1"을
# 유지한다.
CLIENT_REQUEST_PROMPT_VERSION = "v2-orchestrator"


async def _ensure_run(message: SourceMessage) -> AnalysisRun:
    input_hash = _hash(message.contentHash + message.sourceKey)
    existing = await AnalysisRun.find_one(
        AnalysisRun.ownerId == message.ownerId, AnalysisRun.targetType == "CLIENT_REQUEST",
        AnalysisRun.inputHash == input_hash, AnalysisRun.promptVersion == CLIENT_REQUEST_PROMPT_VERSION,
    )
    if existing:
        return existing
    run = AnalysisRun(
        ownerId=message.ownerId, projectId=message.projectId, targetType="CLIENT_REQUEST",
        sourceMessageId=message.id, status="PENDING", inputHash=input_hash, model=EXTRACT_MODEL,
        promptVersion=CLIENT_REQUEST_PROMPT_VERSION,
    )
    try:
        await run.insert()
    except DuplicateKeyError:
        existing = await AnalysisRun.find_one(
            AnalysisRun.ownerId == message.ownerId, AnalysisRun.targetType == "CLIENT_REQUEST",
            AnalysisRun.inputHash == input_hash, AnalysisRun.promptVersion == CLIENT_REQUEST_PROMPT_VERSION,
        )
        if existing:
            return existing
        raise
    return run


async def _ensure_material_run(material: ProjectMaterial) -> AnalysisRun:
    input_hash = _hash((material.contentHash or material.fileName) + str(material.id))
    existing = await AnalysisRun.find_one(
        AnalysisRun.ownerId == material.ownerId, AnalysisRun.targetType == "MATERIAL_CLASSIFICATION",
        AnalysisRun.inputHash == input_hash, AnalysisRun.promptVersion == "v1",
    )
    if existing:
        return existing
    run = AnalysisRun(ownerId=material.ownerId, projectId=material.projectId, targetType="MATERIAL_CLASSIFICATION",
                      materialId=material.id, inputHash=input_hash, model=EXTRACT_MODEL)
    try:
        await run.insert()
    except DuplicateKeyError:
        existing = await AnalysisRun.find_one(
            AnalysisRun.ownerId == material.ownerId, AnalysisRun.targetType == "MATERIAL_CLASSIFICATION",
            AnalysisRun.inputHash == input_hash, AnalysisRun.promptVersion == "v1",
        )
        if existing:
            return existing
        raise
    return run


async def _save_client_requests(
    message: SourceMessage, run: AnalysisRun, analyzed: list[AnalyzedRequest]
) -> None:
    """요청 N건을 ordinal 순서로 upsert한다.

    responseStatus는 건드리지 않는다. 사람이 대응 완료로 바꿔둔 카드가 재분석
    때문에 다시 대기로 돌아가면 안 된다.
    """
    for ordinal, item in enumerate(analyzed):
        values = dict(
            analysisRunId=run.id,
            sourceChannel=message.sourceChannel,
            senderDisplay=message.senderDisplay,
            occurredAt=message.occurredAt,
            aiProcessingStatus="COMPLETED",
            summaryTitle=item.summaryTitle,
            aiDecisionStatus=item.decision,
            decisionReason=item.reason or None,
            requestEvidence=(
                [{"quote": item.requestQuote, "sourceMessageId": str(message.id)}]
                if item.requestQuote
                else []
            ),
            documentEvidence=(
                [{"quote": item.documentQuote, "documentId": item.documentId}]
                if item.documentQuote and item.documentId
                else []
            ),
            updatedAt=_now(),
        )
        existing = await ClientRequest.find_one(
            ClientRequest.ownerId == message.ownerId,
            ClientRequest.sourceMessageId == message.id,
            ClientRequest.requestOrdinal == ordinal,
        )
        if existing:
            for key, value in values.items():
                setattr(existing, key, value)
            await existing.save()
        else:
            await ClientRequest(
                ownerId=message.ownerId,
                projectId=message.projectId,
                sourceMessageId=message.id,
                requestOrdinal=ordinal,
                **values,
            ).insert()

    # 재분석에서 요청 수가 줄면 앞선 분석이 남긴 카드를 지운다. 그대로 두면
    # 원문에 없는 요청이 화면에 계속 남는다.
    stale = await ClientRequest.find(
        ClientRequest.ownerId == message.ownerId,
        ClientRequest.sourceMessageId == message.id,
        ClientRequest.requestOrdinal >= len(analyzed),
    ).to_list()
    for item in stale:
        await item.delete()


async def analyze_source_run(run_id: str):
    """원문 한 건을 분석해 ClientRequest로 남긴다. BackgroundTasks가 부른다.

    판단은 infra/llm/orchestrator.py가 하고 여기서는 저장만 한다. 라우트 파일이
    LLM을 직접 부르지 않는다.
    """
    run = await AnalysisRun.get(PydanticObjectId(run_id))
    if run is None or run.sourceMessageId is None:
        return
    message = await SourceMessage.get(run.sourceMessageId)
    if message is None:
        return
    run.status, run.startedAt, run.updatedAt = "PROCESSING", _now(), _now()
    await run.save()
    try:
        analyzed = await analyze_request_message(
            owner_id=message.ownerId,
            project_id=message.projectId,
            raw_text=message.rawText,
        )
        await _save_client_requests(message, run, analyzed)
        await sync_requirements_from_requests(
            owner_id=message.ownerId, project_id=message.projectId
        )
        run.status, run.completedAt, run.updatedAt = "COMPLETED", _now(), _now()
        await run.save()
    except Exception:
        run.status, run.errorCode, run.completedAt, run.updatedAt = "FAILED", "ANALYSIS_FAILED", _now(), _now()
        await run.save()


def _document_type_from_name(file_name: str) -> DocumentType:
    """파일명으로 문서 종류를 정한다. 모델을 못 쓸 때의 폴백이다."""
    name = file_name.lower()
    if "계약" in name or "contract" in name:
        return "CONTRACT"
    if "제안" in name or "proposal" in name:
        return "PROPOSAL"
    if "요구" in name or "requirement" in name:
        return "REQUIREMENTS"
    if "회의" in name or "meeting" in name:
        return "MEETING_NOTES"
    return "OTHER"


async def classify_material_run(run_id: str):
    run = await AnalysisRun.get(PydanticObjectId(run_id))
    if run is None or run.materialId is None:
        return
    material = await ProjectMaterial.get(run.materialId)
    if material is None:
        return
    material.classificationStatus = "PROCESSING"
    material.updatedAt = _now()
    await material.save()
    run.status, run.startedAt, run.updatedAt = "PROCESSING", _now(), _now()
    await run.save()
    try:
        kind: DocumentType | None = None
        if material.extractedText:
            classified = await run_json(
                system_prompt=PROJECT_MATERIAL_SYSTEM_PROMPT,
                user_content=f"파일명: {material.fileName}\n텍스트: {material.extractedText[:8000]}",
                schema=MaterialClassificationResult,
            )
            kind = classified.documentType if classified else None
        # 추출된 텍스트가 없거나 모델이 답하지 못하면 파일명으로 정한다.
        # 분류 자체를 실패로 두면 자료 탭이 비어 보인다.
        if kind is None:
            kind = _document_type_from_name(material.fileName)
        material.documentType, material.classificationStatus, material.updatedAt = kind, "COMPLETED", _now()
        await material.save()
        run.status, run.completedAt, run.updatedAt = "COMPLETED", _now(), _now()
        await run.save()
    except Exception:
        material.classificationStatus, material.updatedAt = "FAILED", _now()
        await material.save()
        run.status, run.errorCode, run.completedAt, run.updatedAt = "FAILED", "CLASSIFICATION_FAILED", _now(), _now()
        await run.save()


async def _store_material_original(material: ProjectMaterial, connection_id: str) -> None:
    """Slack 원본 파일을 한 번만 내려받아 S3에 올린다.

    실패해도 예외를 위로 던지지 않는다. 원본 저장은 부가 기능이라, 이게
    실패한다고 자료 등록·분류까지 막히면 안 된다.
    """
    try:
        connection = await slack_connection(str(material.ownerId), material.connectionId or connection_id)
        # sync 호출부가 이미 연결을 확인했지만, 이 함수는 독립적으로도 안전해야 한다.
        if connection is None or not material.providerFileId:
            return
        downloaded = await fetch_file(bot_token=access_token(connection), file_id=material.providerFileId)
    except Exception:
        return
    key = f"materials/{material.ownerId}/{material.projectId}/{material.id}/{downloaded.fileName}"
    stored_key = put_object(key, downloaded.content, downloaded.contentType)
    if stored_key is None:
        return
    material.storageKey = stored_key
    material.mimeType = downloaded.contentType
    material.sizeBytes = len(downloaded.content)
    material.updatedAt = _now()
    await material.save()


@router.post("/projects/{project_id}/git/ask", tags=["ingest"])
async def ask_git_repository(
    project_id: PydanticObjectId, body: GitAskRequest,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    link = await ProjectSourceLink.find_one(
        ProjectSourceLink.ownerId == current_user.id,
        ProjectSourceLink.projectId == project.id,
        ProjectSourceLink.sourceChannel == "GITHUB",
    )
    if link is None or not link.repoFullName:
        return fail("먼저 이 프로젝트에 GitHub 저장소를 연결해 주세요.", 404)
    # 사용자가 등록한 PAT를 먼저 쓰고, 없으면 서버 기본 토큰(공개 저장소)으로 떨어진다.
    connection = await github_connection(str(current_user.id))
    token = access_token(connection) if connection else None
    answer = await ask_repository(
        repo_full_name=link.repoFullName, question=body.question, token=token
    )
    return ok({"answer": answer, "repoFullName": link.repoFullName})


@router.post("/projects/{project_id}/source-links/{source_link_id}/sync", tags=["ingest"])
async def sync_source_link(
    project_id: PydanticObjectId, source_link_id: PydanticObjectId,
    background_tasks: BackgroundTasks,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    link = await ProjectSourceLink.find_one(
        ProjectSourceLink.id == source_link_id, ProjectSourceLink.ownerId == current_user.id,
        ProjectSourceLink.projectId == project.id,
    )
    if link is None:
        return fail("연결 대상을 찾을 수 없습니다.", 404)
    new_count = 0
    run_ids: list[str] = []
    if link.sourceChannel == "GITHUB":
        return fail(
            "GitHub 연결은 동기화 대신 /git/ask로 질문하세요.", 400
        )
    try:
        if link.sourceChannel == "GMAIL":
            connection = await latest_gmail_connection(str(current_user.id))
            if connection is None:
                return fail("Gmail이 연결되어 있지 않습니다.", 404)
            connection, token = await _gmail_connection_token(connection)
            emails = await fetch_recent(
                access_token=token, max_messages=50, counterparty=link.counterpartyEmail
            )
            for email in emails:
                if not email.body.strip():
                    continue
                direction = "SENT" if email.from_.address.lower() == connection.externalId.lower() else "RECEIVED"
                message, created = await _upsert_source_message(
                    link, connection.externalId, source_key=f"{connection.externalId}:{email.id}",
                    provider_id=email.id, provider_thread_id=email.threadId,
                    sender_id=email.from_.address, sender_display=email.from_.name or email.from_.address,
                    conversation=email.subject, direction=direction, raw_text=email.body,
                    occurred_at=_utc_datetime(email.sentAt), attachments=[],
                )
                if created:
                    new_count += 1
                    run = await _ensure_run(message)
                    run_ids.append(str(run.id))
                    background_tasks.add_task(analyze_source_run, str(run.id))
        else:
            if not link.teamId or not link.channelId:
                return fail("Slack 연결에는 teamId와 channelId가 필요합니다.")
            connection = await slack_connection(str(current_user.id), link.teamId)
            if connection is None:
                return fail("Slack 워크스페이스가 연결되어 있지 않습니다.", 404)
            messages = await fetch_history(bot_token=access_token(connection), channel_id=link.channelId)
            for item in messages:
                if not item.text.strip() and not item.files:
                    continue
                message, created = await _upsert_source_message(
                    link, connection.externalId, source_key=f"{link.teamId}:{link.channelId}:{item.id}",
                    provider_id=item.id, provider_thread_id=item.id,
                    sender_id=item.userId, sender_display=item.userName,
                    conversation=f"#{link.displayName}", direction="RECEIVED", raw_text=item.text,
                    occurred_at=_utc_datetime(item.sentAt), attachments=[f.fileId for f in item.files],
                )
                if created:
                    new_count += 1
                    run = await _ensure_run(message)
                    run_ids.append(str(run.id))
                    background_tasks.add_task(analyze_source_run, str(run.id))
                    for file in item.files:
                        material = ProjectMaterial(
                            ownerId=current_user.id, projectId=project.id, sourceMessageId=message.id,
                            connectionId=connection.externalId, providerFileId=file.fileId,
                            fileName=file.name, direction="RECEIVED", communicatedAt=message.occurredAt,
                            contentHash=_hash(file.fileId), classificationStatus="PENDING",
                        )
                        created_material = True
                        try:
                            await material.insert()
                        except DuplicateKeyError:
                            created_material = False
                            material = await ProjectMaterial.find_one(
                                ProjectMaterial.ownerId == current_user.id,
                                ProjectMaterial.connectionId == connection.externalId,
                                ProjectMaterial.providerFileId == file.fileId,
                            )
                        # 새로 만든 자료만 원본을 내려받는다. 이미 있던 자료를
                        # 재동기화 때마다 다시 내려받지 않는다.
                        if material and created_material and has_s3():
                            await _store_material_original(material, connection.externalId)
                        if material:
                            material_run = await _ensure_material_run(material)
                            background_tasks.add_task(classify_material_run, str(material_run.id))
    except (IntegrationError, TokenEncryptionError, RuntimeError, ValueError):
        return fail("외부 채널에서 데이터를 가져오지 못했습니다. 연결 상태를 확인해 주세요.", 502)
    return ok({"sourceMessageCount": new_count, "newMessageCount": new_count, "analysisRunIds": run_ids})


@router.get("/analysis-runs/{analysis_run_id}", tags=["ingest"])
async def analysis_run(analysis_run_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    run = await AnalysisRun.find_one(AnalysisRun.id == analysis_run_id, AnalysisRun.ownerId == current_user.id)
    if run is None:
        return fail("분석 실행을 찾을 수 없습니다.", 404)
    return ok(run.model_dump(mode="json", exclude={"id", "ownerId"}) | {"analysisRunId": str(run.id)})


@router.get("/projects/{project_id}/requirements", tags=["agreement"])
async def project_requirements(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirements = await Requirement.find(Requirement.ownerId == current_user.id, Requirement.projectId == project.id).to_list()
    from app.public_data import public_requirement
    return ok([public_requirement(item) for item in requirements])


@router.get("/projects/{project_id}/contract", tags=["agreement"])
async def project_contract(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    contract = await Contract.find(Contract.ownerId == current_user.id, Contract.projectId == project.id).sort(-Contract.version).first_or_none()
    if contract is None:
        return fail("등록된 계약이 없습니다.", 404)
    from app.public_data import public_contract
    return ok(public_contract(contract))


@router.post("/projects/{project_id}/contract", tags=["agreement"])
async def create_project_contract(project_id: PydanticObjectId, body: ContractState, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    current = await Contract.find_one(Contract.ownerId == current_user.id, Contract.projectId == project.id, Contract.version == 1)
    if current is not None:
        return fail("이미 등록된 계약이 있습니다.", 409)
    if body.version != 1 or body.appliedRequirementId is not None:
        return fail("최초 계약은 1버전으로 등록해 주세요.")
    contract = Contract(**body.model_dump(), ownerId=current_user.id, projectId=project.id)
    await contract.insert()
    return ok(__import__("app.public_data", fromlist=["public_contract"]).public_contract(contract))


@router.post("/projects/{project_id}/contract/apply", tags=["agreement"])
async def apply_project_contract(project_id: PydanticObjectId, body: ContractApplyRequest, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirement = await Requirement.find_one(Requirement.id == body.requirementId, Requirement.ownerId == current_user.id, Requirement.projectId == project.id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    contract = await Contract.find(Contract.ownerId == current_user.id, Contract.projectId == project.id).sort(-Contract.version).first_or_none()
    if contract is None:
        return fail("등록된 계약이 없습니다.", 404)
    applied = await Contract.find_one(Contract.ownerId == current_user.id, Contract.projectId == project.id, Contract.appliedRequirementId == str(body.requirementId))
    if applied:
        previous = await Contract.find_one(Contract.ownerId == current_user.id, Contract.projectId == project.id, Contract.version == applied.version - 1)
        if previous:
            from app.public_data import public_contract
            return ok({"contract": public_contract(applied), "diff": diff_contract(previous, applied)})
    try:
        next_state = apply_to_contract(contract, requirement, str(requirement.id))
    except ValueError as exc:
        return fail(str(exc))
    next_contract = Contract(**next_state.model_dump(), ownerId=current_user.id, projectId=project.id)
    try:
        await next_contract.insert()
    except DuplicateKeyError:
        return fail("계약이 동시에 변경됐습니다. 다시 시도해 주세요.", 409)
    from app.public_data import public_contract
    return ok({"contract": public_contract(next_contract), "diff": diff_contract(contract, next_state)})


async def _project_requirement(project, requirement_id: PydanticObjectId, owner_id: PydanticObjectId):
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


@router.post("/projects/{project_id}/requirements/{requirement_id}/questions", tags=["agreement"])
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
        return fail("확인 질문을 만들지 못했습니다. 다시 시도해 주세요.", 502)
    return ok({"questions": questions})


@router.post("/projects/{project_id}/requirements/{requirement_id}/reply", tags=["agreement"])
async def requirement_reply(
    project_id: PydanticObjectId, requirement_id: PydanticObjectId, body: ReplyDraftRequest,
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
        return fail("답변 초안을 만들지 못했습니다. 다시 시도해 주세요.", 502)
    return ok({"draft": draft})


@router.get("/projects/{project_id}/requirements/{requirement_id}/allowed", tags=["agreement"])
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
    from core.state_machine import TRANSITIONS
    return ok({"allowed": list(TRANSITIONS[requirement.status])})


@router.post("/projects/{project_id}/requirements/{requirement_id}/transition", tags=["agreement"])
async def transition_project_requirement(
    project_id: PydanticObjectId, requirement_id: PydanticObjectId, body: RequirementTransitionRequest,
    current_user: User | None = Depends(get_current_user),
):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirement = await Requirement.find_one(Requirement.id == requirement_id, Requirement.ownerId == current_user.id, Requirement.projectId == project.id)
    if requirement is None:
        return fail("해당 요구사항을 찾을 수 없습니다.", 404)
    from core.state_machine import transition
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
    from app.public_data import public_requirement
    return ok(public_requirement(requirement))
