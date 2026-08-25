"""프로젝트 중심의 수집·요청·자료 API.

시연 MVP는 외부 provider에서 가져온 원문을 서버에 저장하고, FastAPI
BackgroundTasks로 분석한다. 프론트는 provider API를 직접 호출하지 않는다.
"""

import hashlib
import os
import unicodedata
from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import quote

from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.api.slack import SAFE_INLINE_IMAGE_TYPES
from app.auth import get_current_user
from app.integration_store import (
    access_token, github_connection, latest_gmail_connection, slack_connection,
)
from app.public_data import public_material, public_project
from app.requirement_sync import sync_requirements_from_requests
from app.response import fail, ok
from core.channel_data import RawEmail
from core.contract_ops import apply_to_contract, diff_contract
from core.domain import ContractState
from core.project_data import (
    DocumentType, ProcessingStatus, ProjectSort, ProjectStatus,
    RelatedFile, SourceChannel, TicketSolution, TicketStatus,
)
from infra.integrations import IntegrationError
from infra.integrations.gmail import (
    GMAIL_SCOPES, fetch_attachment, fetch_message_attachment, fetch_recent, refresh_access_token,
)
from infra.integrations.slack import fetch_file, fetch_history
from infra.llm.client import EXTRACT_MODEL
from infra.llm.harness import run_json
from infra.llm.orchestrator import AnalyzedRequest, analyze_request_message
from infra.llm.subagents.checklist import build_checklist
from infra.llm.subagents.git_explore import ask_repository
from infra.llm.subagents.reply_draft import build_reply_draft
from infra.llm.subagents.ticket_advice import build_ticket_advice
from infra.llm.prompts import PROJECT_MATERIAL_SYSTEM_PROMPT
from infra.llm.schemas import MaterialClassificationResult
from infra.security.provider_tokens import TokenEncryptionError
from infra.storage.s3 import get_object, has_s3, put_object
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


class MaterialDiscoveryRequest(BaseModel):
    """화면이 이미 받아온 메일 목록에서 첨부만 자료로 등록한다.

    Gmail을 다시 부르지 않는다. 요구사항 추출·고객 이메일 탭이 이미
    /api/email/messages로 받아 둔 결과를 그대로 보낸다.
    """

    emails: list[RawEmail] = Field(max_length=200)


async def get_owned_project(project_id: PydanticObjectId, user: User | None) -> Project | None:
    if user is None:
        return None
    return await Project.find_one(Project.id == project_id, Project.ownerId == user.id)


async def _unanswered_count(project_id: PydanticObjectId, owner_id: PydanticObjectId) -> int:
    return await ClientRequest.find(
        ClientRequest.ownerId == owner_id,
        ClientRequest.projectId == project_id,
        ClientRequest.ticketStatus == "active",
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
        "solution": item.solution.model_dump(mode="json") if item.solution else None,
        "sourceText": message.rawText if message else None,
        "conversationDisplay": message.conversationDisplay if message else None,
    })
    return ok(data)


class ReplyDraftRequest(BaseModel):
    selectedItems: list[str] = Field(default_factory=list, max_length=6)
    tone: Literal["friendly", "professional", "concise", "firm"] = "professional"


class TicketStatusRequest(BaseModel):
    ticketStatus: TicketStatus


async def _owned_request(request_id: PydanticObjectId, owner_id: PydanticObjectId) -> ClientRequest | None:
    return await ClientRequest.find_one(
        ClientRequest.id == request_id, ClientRequest.ownerId == owner_id
    )


@router.post("/requests/{request_id}/solution", tags=["request"])
async def ticket_solution(
    request_id: PydanticObjectId,
    refresh: bool = False,
    current_user: User | None = Depends(get_current_user),
):
    """티켓 하나의 솔루션 패키지를 만든다. 조언·이유·근거 조문·관련 파일이다.

    한 번 만들면 저장하고 다음부터는 그대로 돌려준다. 조언과 근거는 티켓이
    바뀌지 않는 한 달라질 이유가 없어서, 화면에 들어올 때마다 다시 만들면
    토큰만 쓴다. 다시 만들려면 refresh=true를 준다.

    답변 초안은 여기 없다. 말투마다 따로 만드는 값이라 /reply-draft가 맡는다.
    """
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    item = await _owned_request(request_id, current_user.id)
    if item is None:
        return fail("요청을 찾을 수 없습니다.", 404)
    if item.solution is not None and not refresh:
        return ok(item.solution.model_dump(mode="json"))

    advice = await build_ticket_advice(
        owner_id=item.ownerId,
        project_id=item.projectId,
        summary_title=item.summaryTitle or "제목 없는 요청",
        decision=item.aiDecisionStatus or "판정 없음",
        request_quote=item.requestEvidence[0].quote if item.requestEvidence else "",
    )

    # 관련 파일은 AI에게 고르게 하지 않는다. 이 프로젝트에 어떤 자료가 있는지는
    # DB가 아는 사실이라 추론할 대상이 아니다.
    materials = (
        await ProjectMaterial.find(
            ProjectMaterial.ownerId == item.ownerId,
            ProjectMaterial.projectId == item.projectId,
        )
        .sort(-ProjectMaterial.communicatedAt)
        .limit(5)
        .to_list()
    )
    item.solution = TicketSolution(
        adviceMessage=advice.adviceMessage,
        adviceReason=advice.adviceReason,
        basisQuote=advice.basisQuote,
        basisDocumentId=advice.basisDocumentId,
        relatedFiles=[
            RelatedFile(
                materialId=str(material.id),
                fileName=material.fileName,
                documentType=material.documentType,
            )
            for material in materials
        ],
        generatedAt=_now(),
    )
    item.updatedAt = _now()
    await item.save()
    return ok(item.solution.model_dump(mode="json"))


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


@router.patch("/requests/{request_id}/ticket-status", tags=["request"])
async def update_ticket_status(
    request_id: PydanticObjectId,
    body: TicketStatusRequest,
    current_user: User | None = Depends(get_current_user),
):
    """사람이 티켓 상태를 바꾼다. AI는 관여하지도, 제안하지도 않는다.

    대응이 끝났는지는 대화 밖에서 일어나는 일이라 메시지만 보고 알 수 없다.
    자동화하면 열려 있어야 할 티켓이 닫히고, 그게 곧 놓친 요청이 된다.
    active에서 나가는 유일한 경로다.
    """
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    item = await _owned_request(request_id, current_user.id)
    if item is None:
        return fail("요청을 찾을 수 없습니다.", 404)
    item.ticketStatus = body.ticketStatus
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


@router.post("/projects/{project_id}/materials/discover", tags=["request"])
async def discover_materials(
    project_id: PydanticObjectId, body: MaterialDiscoveryRequest,
    background_tasks: BackgroundTasks,
    current_user: User | None = Depends(get_current_user),
):
    """메일 목록에서 첨부를 찾아 자료로 등록한다.

    요구사항 추출·고객 이메일 탭이 화면에 메일을 띄울 때마다 함께 부른다.
    소스링크를 만들고 동기화하는 절차 없이도 아카이브가 채워진다.
    """
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    connection = await latest_gmail_connection(str(current_user.id))
    if connection is None:
        return fail("Gmail이 연결되어 있지 않습니다.", 404)

    discovered = 0
    for email in body.emails:
        if not email.attachments:
            continue
        direction = "SENT" if email.from_.address.lower() == connection.externalId.lower() else "RECEIVED"
        sender_display = email.from_.name or email.from_.address
        for attachment in email.attachments:
            provider_file_id = f"{email.id}:{attachment.id}"
            material = ProjectMaterial(
                ownerId=current_user.id, projectId=project.id,
                sourceChannel="GMAIL", connectionId=connection.externalId,
                providerFileId=provider_file_id,
                conversationTitle=email.subject, senderDisplay=sender_display,
                fileName=attachment.filename, mimeType=attachment.mimeType,
                sizeBytes=attachment.sizeBytes, direction=direction,
                communicatedAt=_utc_datetime(email.sentAt),
                contentHash=_hash(provider_file_id), classificationStatus="PENDING",
            )
            try:
                await material.insert()
            except DuplicateKeyError:
                # 이미 있는 자료다. 동기화(sync_source_link)로 먼저 만들어졌으면
                # 대화 제목이 비어 있을 수 있으니 그때만 채워 준다.
                existing = await ProjectMaterial.find_one(
                    ProjectMaterial.ownerId == current_user.id,
                    ProjectMaterial.connectionId == connection.externalId,
                    ProjectMaterial.providerFileId == provider_file_id,
                )
                if existing is not None and existing.conversationTitle is None:
                    existing.conversationTitle = email.subject
                    existing.senderDisplay = sender_display
                    existing.updatedAt = _now()
                    await existing.save()
                continue
            discovered += 1
            material_run = await _ensure_material_run(material)
            background_tasks.add_task(classify_material_run, str(material_run.id))
    return ok({"discoveredCount": discovered})


@router.get("/projects/{project_id}/materials/{material_id}/file", tags=["request"])
async def download_material_file(
    project_id: PydanticObjectId, material_id: PydanticObjectId,
    current_user: User | None = Depends(get_current_user),
):
    """자료 원본을 그대로 내려준다.

    S3에 이미 올려둔 원본이 있으면 그걸 쓴다. 없어도 Gmail 자료라면 그
    자리에서 다시 받아온다 — 아카이브 등록(discover) 시점에는 목록만 만들고
    원본을 미리 내려받지 않으므로, 실제로 열어볼 때 이 경로를 탄다. 처음
    받아오는 김에 S3가 있으면 캐시해 둬서 다음부터는 바로 나가게 한다.
    """
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    material = await ProjectMaterial.find_one(
        ProjectMaterial.id == material_id, ProjectMaterial.ownerId == current_user.id,
        ProjectMaterial.projectId == project.id,
    )
    if material is None:
        return fail("해당 자료를 찾을 수 없습니다.", 404)

    content = get_object(material.storageKey) if material.storageKey else None
    if content is None:
        content = await _fetch_material_live(material, current_user)
        if content is None:
            return fail("원본 파일이 아직 없습니다. 파일이 크거나 저장에 실패했을 수 있습니다.", 404)

    # Slack 파일 응답(app/api/slack.py)과 같은 정책이다. 이미지 몇 종만
    # inline을 허락하고 나머지는 브라우저가 직접 렌더링하지 않도록 강제한다.
    # 화면의 PDF·DOCX 뷰어는 이 응답을 fetch로 받아 blob으로 다루기 때문에
    # Content-Disposition·Content-Type이 attachment/octet-stream이어도 상관없다
    # — 화면이 이미 알고 있는 MIME 타입으로 다시 씌워서 보여준다.
    inline = (material.mimeType or "") in SAFE_INLINE_IMAGE_TYPES
    encoded_name = quote(material.fileName)
    return Response(
        content=content,
        media_type=material.mimeType if inline else "application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{encoded_name}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
CLIENT_REQUEST_PROMPT_VERSION = "v3-ticket-match"


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


async def _attach_to_ticket(
    ticket: ClientRequest, message: SourceMessage, item: AnalyzedRequest
) -> None:
    """후속 인바운드를 기존 티켓에 붙인다.

    제목과 판정은 덮어쓰지 않는다. 티켓의 정체성은 처음 만들어진 요청이 정하고,
    뒤따라온 메시지는 근거를 보태는 역할이다. 덮어쓰면 "로고 색 변경" 티켓이
    마지막 메시지 제목으로 바뀌어 사람이 추적을 잃는다.
    """
    if message.id not in ticket.sourceMessageIds:
        ticket.sourceMessageIds.append(message.id)
    if item.requestQuote:
        ticket.requestEvidence.append(
            {"quote": item.requestQuote, "sourceMessageId": str(message.id)}
        )
    ticket.updatedAt = _now()
    await ticket.save()


async def _save_client_requests(
    message: SourceMessage, run: AnalysisRun, analyzed: list[AnalyzedRequest]
) -> None:
    """분석 결과를 티켓으로 남긴다.

    기존 티켓에 매칭된 요청은 그 티켓에 붙이고, 나머지만 새 티켓으로 만든다.
    ticketStatus는 건드리지 않는다. 사람이 done으로 바꿔둔 티켓이 재분석 때문에
    다시 열리면 안 된다.
    """
    new_ordinal = 0
    for item in analyzed:
        if item.matchedTicketId:
            ticket = await ClientRequest.find_one(
                ClientRequest.id == PydanticObjectId(item.matchedTicketId),
                ClientRequest.ownerId == message.ownerId,
                ClientRequest.projectId == message.projectId,
            )
            if ticket is not None:
                await _attach_to_ticket(ticket, message, item)
                continue
            # 매칭된 티켓이 사라졌으면 새로 만든다.

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
            ClientRequest.requestOrdinal == new_ordinal,
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
                sourceMessageIds=[message.id],
                requestOrdinal=new_ordinal,
                **values,
            ).insert()
        new_ordinal += 1

    # 재분석에서 새 티켓 수가 줄면 앞선 분석이 남긴 카드를 지운다. 그대로 두면
    # 원문에 없는 요청이 화면에 계속 남는다.
    stale = await ClientRequest.find(
        ClientRequest.ownerId == message.ownerId,
        ClientRequest.sourceMessageId == message.id,
        ClientRequest.requestOrdinal >= new_ordinal,
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
    """파일명으로 문서 종류를 정한다. 모델을 못 쓸 때의 폴백이다.

    NFC로 정규화하고 비교한다. Gmail 첨부는 보낸 사람 OS에 따라 한글을
    자모 분해형(NFD)으로 줄 때가 있는데, 그러면 화면엔 '계약서'로 똑같이
    보여도 "계약" in name이 조용히 실패한다.
    """
    name = unicodedata.normalize("NFC", file_name).lower()
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


# Gmail API가 첨부 하나에 매기는 상한(25MB)보다 여유 있게 낮춰 둔다. 서버가
# 큰 파일을 통째로 메모리에 올렸다가 S3에 넣는 비용을 줄이기 위해서다.
# 이 크기를 넘는 첨부도 목록에는 남는다 — 원본만 못 받아올 뿐이다.
MAX_GMAIL_ATTACHMENT_BYTES = 10 * 1024 * 1024


async def _store_gmail_attachment_original(
    material: ProjectMaterial, access_token_value: str, message_id: str, attachment_id: str,
) -> None:
    """Gmail 첨부 원본을 한 번만 내려받아 S3에 올린다.

    attachment_id는 반드시 방금 목록 조회에서 받은 값이어야 한다. Gmail의
    attachmentId는 messages.get을 부를 때마다 새로 발급되는 일회성 토큰이라,
    material.providerFileId(파트 위치를 가리키는 안정적인 값)에서 되짚어 낼
    수 없다 — 저장해 뒀다가 나중에 쓰면 이미 무효한 값이다.

    _store_material_original(Slack)과 같은 이유로 예외를 위로 던지지 않는다.
    """
    if material.sizeBytes is None:
        return
    if material.sizeBytes > MAX_GMAIL_ATTACHMENT_BYTES:
        return
    try:
        downloaded = await fetch_attachment(
            access_token=access_token_value, message_id=message_id, attachment_id=attachment_id,
            file_name=material.fileName, mime_type=material.mimeType or "application/octet-stream",
        )
    except Exception:
        return
    key = f"materials/{material.ownerId}/{material.projectId}/{material.id}/{downloaded.fileName}"
    stored_key = put_object(key, downloaded.content, downloaded.contentType)
    if stored_key is None:
        return
    material.storageKey = stored_key
    material.updatedAt = _now()
    await material.save()


async def _fetch_material_live(material: ProjectMaterial, owner: User) -> bytes | None:
    """S3에 원본이 없는 Gmail 자료를 그 자리에서 받아온다. 화면이 눌렀을 때만 부른다.

    discover 단계는 목록만 만들고 원본을 미리 내려받지 않으므로, 실제로
    열어보는 이 시점이 첫 다운로드다. 성공하면 다음부터는 S3에서 바로
    나가도록 캐시해 둔다.
    """
    if (
        material.sourceChannel != "GMAIL"
        or material.connectionId is None
        or material.providerFileId is None
        or ":" not in material.providerFileId
    ):
        return None
    if material.sizeBytes is not None and material.sizeBytes > MAX_GMAIL_ATTACHMENT_BYTES:
        return None
    connection = await latest_gmail_connection(str(owner.id))
    if connection is None:
        return None
    try:
        connection, token = await _gmail_connection_token(connection)
        message_id, part_id = material.providerFileId.split(":", 1)
        # attachmentId는 일회성 토큰이라 discover 시점 값을 못 쓴다. part_id로
        # 메시지를 다시 찾아 이번 토큰을 새로 받는다.
        downloaded = await fetch_message_attachment(
            access_token=token, message_id=message_id, part_id=part_id,
        )
    except Exception:
        return None

    if has_s3():
        key = f"materials/{material.ownerId}/{material.projectId}/{material.id}/{downloaded.fileName}"
        stored_key = put_object(key, downloaded.content, downloaded.contentType)
        if stored_key is not None:
            material.storageKey = stored_key
            material.updatedAt = _now()
            await material.save()
    return downloaded.content


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
                # 본문이 없어도 첨부만 있는 메일은 있다(제안서만 달랑 보내는 경우).
                # 둘 다 없을 때만 건너뛴다.
                if not email.body.strip() and not email.attachments:
                    continue
                direction = "SENT" if email.from_.address.lower() == connection.externalId.lower() else "RECEIVED"
                message, created = await _upsert_source_message(
                    link, connection.externalId, source_key=f"{connection.externalId}:{email.id}",
                    provider_id=email.id, provider_thread_id=email.threadId,
                    sender_id=email.from_.address, sender_display=email.from_.name or email.from_.address,
                    conversation=email.subject, direction=direction, raw_text=email.body,
                    occurred_at=_utc_datetime(email.sentAt),
                    attachments=[a.id for a in email.attachments],
                )
                if created:
                    new_count += 1
                    if email.body.strip():
                        run = await _ensure_run(message)
                        run_ids.append(str(run.id))
                        background_tasks.add_task(analyze_source_run, str(run.id))
                    for attachment in email.attachments:
                        material = ProjectMaterial(
                            ownerId=current_user.id, projectId=project.id, sourceMessageId=message.id,
                            sourceChannel="GMAIL", connectionId=connection.externalId,
                            providerFileId=f"{email.id}:{attachment.id}",
                            conversationTitle=email.subject,
                            senderDisplay=email.from_.name or email.from_.address,
                            fileName=attachment.filename, mimeType=attachment.mimeType,
                            sizeBytes=attachment.sizeBytes, direction=direction,
                            communicatedAt=message.occurredAt,
                            contentHash=_hash(f"{email.id}:{attachment.id}"),
                            classificationStatus="PENDING",
                        )
                        created_material = True
                        try:
                            await material.insert()
                        except DuplicateKeyError:
                            created_material = False
                            material = await ProjectMaterial.find_one(
                                ProjectMaterial.ownerId == current_user.id,
                                ProjectMaterial.connectionId == connection.externalId,
                                ProjectMaterial.providerFileId == f"{email.id}:{attachment.id}",
                            )
                        if material and created_material and has_s3():
                            await _store_gmail_attachment_original(
                                material, token, email.id, attachment.attachmentId
                            )
                        if material:
                            material_run = await _ensure_material_run(material)
                            background_tasks.add_task(classify_material_run, str(material_run.id))
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
                            sourceChannel="SLACK", connectionId=connection.externalId,
                            providerFileId=file.fileId,
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

