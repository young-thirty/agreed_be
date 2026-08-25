"""프로젝트 중심의 수집·요청·자료 API.

시연 MVP는 외부 provider에서 가져온 원문을 서버에 저장하고, FastAPI
BackgroundTasks로 분석한다. 프론트는 provider API를 직접 호출하지 않는다.
"""

import hashlib
import json
import os
from datetime import date, datetime, timezone
from typing import Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.auth import get_current_user
from app.integration_store import access_token, latest_gmail_connection, slack_connection
from app.public_data import public_material, public_project
from app.response import fail, ok
from core.contract_ops import apply_to_contract, diff_contract
from core.domain import ContractState, Decision, RequirementStatus, Tone, status_change
from core.project_data import (
    AiDecisionStatus, DocumentType, ProcessingStatus, ProjectSort, ProjectStatus,
    ResponseStatus, SourceChannel,
)
from infra.integrations import IntegrationError
from infra.integrations.gmail import (
    GMAIL_SCOPES, fetch_recent, refresh_access_token,
)
from infra.integrations.slack import fetch_history
from infra.llm.client import EXTRACT_MODEL, get_client, has_api_key
from infra.llm.prompts import (
    PROJECT_ANALYSIS_SYSTEM_PROMPT, PROJECT_MATERIAL_SYSTEM_PROMPT, build_requirement_text,
)
from infra.llm.reply import build_questions, build_reply
from infra.llm.schemas import MaterialClassificationResult, RequestAnalysisResult
from infra.security.provider_tokens import TokenEncryptionError
from models import (
    AnalysisRun, ClientRequest, Contract, Project, ProjectMaterial,
    ProjectSourceLink, Requirement, SourceMessage,
)
from models.client_request import public_client_request
from models.integration import IntegrationConnection
from models.user import User

router = APIRouter(tags=["projects"])


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


async def _llm_json(messages: list[dict[str, str]], schema_type: type[BaseModel]):
    """JSON/Pydantic 검증 실패는 한 번만 재시도한다."""
    last_error: Exception | None = None
    for _ in range(2):
        try:
            response = await get_client().chat.completions.create(
                model=EXTRACT_MODEL, response_format={"type": "json_object"}, messages=messages
            )
            return schema_type.model_validate(json.loads(response.choices[0].message.content or "{}"))
        except Exception as error:
            last_error = error
    assert last_error is not None
    raise last_error


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
    locatorKey: str = Field(min_length=1, max_length=300)


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


@router.post("/projects")
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


@router.get("/projects")
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


@router.get("/projects/{project_id}")
async def project_detail(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    return ok(public_project(project, await _unanswered_count(project.id, current_user.id)))


@router.patch("/projects/{project_id}")
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


@router.patch("/projects/{project_id}/status")
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


@router.get("/projects/{project_id}/requests")
async def project_requests(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requests = await ClientRequest.find(
        ClientRequest.ownerId == current_user.id, ClientRequest.projectId == project.id
    ).sort(-ClientRequest.occurredAt).to_list()
    return ok([public_client_request(item) for item in requests])


@router.get("/requests/{request_id}")
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
        "sourceText": message.rawText if message else None,
        "conversationDisplay": message.conversationDisplay if message else None,
    })
    return ok(data)


@router.get("/projects/{project_id}/materials")
async def project_materials(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    materials = await ProjectMaterial.find(
        ProjectMaterial.ownerId == current_user.id, ProjectMaterial.projectId == project.id
    ).sort(-ProjectMaterial.communicatedAt).to_list()
    return ok([public_material(item) for item in materials])


@router.get("/projects/{project_id}/source-links")
async def source_links(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    links = await ProjectSourceLink.find(
        ProjectSourceLink.ownerId == current_user.id, ProjectSourceLink.projectId == project.id
    ).sort(ProjectSourceLink.createdAt).to_list()
    return ok([item.model_dump(mode="json", exclude={"id", "ownerId"}) | {"sourceLinkId": str(item.id)} for item in links])


@router.post("/projects/{project_id}/source-links")
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
    else:
        if not body.teamId or not body.channelId:
            return fail("Slack 링크에는 teamId와 channelId가 필요합니다.")
        connection = await slack_connection(owner_id, body.teamId)
        if connection is None:
            return fail("연결된 Slack 워크스페이스를 찾을 수 없습니다.", 404)
        body.connectionId = body.connectionId or connection.externalId
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


async def _ensure_run(message: SourceMessage) -> AnalysisRun:
    input_hash = _hash(message.contentHash + message.sourceKey)
    existing = await AnalysisRun.find_one(
        AnalysisRun.ownerId == message.ownerId, AnalysisRun.targetType == "CLIENT_REQUEST",
        AnalysisRun.inputHash == input_hash, AnalysisRun.promptVersion == "v1",
    )
    if existing:
        return existing
    run = AnalysisRun(
        ownerId=message.ownerId, projectId=message.projectId, targetType="CLIENT_REQUEST",
        sourceMessageId=message.id, status="PENDING", inputHash=input_hash, model=EXTRACT_MODEL,
    )
    try:
        await run.insert()
    except DuplicateKeyError:
        existing = await AnalysisRun.find_one(
            AnalysisRun.ownerId == message.ownerId, AnalysisRun.targetType == "CLIENT_REQUEST",
            AnalysisRun.inputHash == input_hash, AnalysisRun.promptVersion == "v1",
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


async def analyze_source_run(run_id: str):
    run = await AnalysisRun.get(PydanticObjectId(run_id))
    if run is None or run.sourceMessageId is None:
        return
    message = await SourceMessage.get(run.sourceMessageId)
    if message is None:
        return
    run.status, run.startedAt, run.updatedAt = "PROCESSING", _now(), _now()
    await run.save()
    try:
        title = None
        decision: AiDecisionStatus | None = None
        quote = ""
        if has_api_key() and message.rawText.strip():
            payload = await _llm_json(
                [
                    {"role": "system", "content": PROJECT_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": message.rawText[:12000]},
                ],
                RequestAnalysisResult,
            )
            title, decision, quote = payload.summaryTitle, payload.decision, payload.quote
        if not title:
            first_line = next((line.strip() for line in message.rawText.splitlines() if line.strip()), "새 클라이언트 요청")
            title = first_line[:80]
            quote = first_line[:200]
            decision = "OUT_OF_SCOPE_COORDINATION_REQUIRED"
        if quote not in message.rawText:
            quote = ""
            decision = "OUT_OF_SCOPE_COORDINATION_REQUIRED"
        document_evidence: list[dict[str, str]] = []
        contract = await Contract.find(
            Contract.ownerId == message.ownerId, Contract.projectId == message.projectId
        ).sort(-Contract.version).first_or_none()
        if contract is not None:
            matched_scope = next((scope for scope in contract.scope if scope and scope in message.rawText), None)
            if matched_scope:
                # 계약 문구가 실제 원문에 있으면 초록으로 올리고, 근거 문장도 함께 저장한다.
                decision = "IN_SCOPE_ACTION_REQUIRED"
                document_evidence = [{"quote": matched_scope, "documentId": str(contract.id)}]
            elif decision == "IN_SCOPE_ACTION_REQUIRED":
                decision = "OUT_OF_SCOPE_COORDINATION_REQUIRED"
        existing = await ClientRequest.find_one(
            ClientRequest.ownerId == message.ownerId, ClientRequest.sourceMessageId == message.id,
            ClientRequest.requestOrdinal == 0,
        )
        values = dict(ownerId=message.ownerId, projectId=message.projectId, sourceMessageId=message.id,
                      analysisRunId=run.id, requestOrdinal=0, sourceChannel=message.sourceChannel,
                      senderDisplay=message.senderDisplay, occurredAt=message.occurredAt,
                      aiProcessingStatus="COMPLETED", summaryTitle=title, aiDecisionStatus=decision,
                      requestEvidence=[] if not quote else [{"quote": quote, "sourceMessageId": str(message.id)}],
                      documentEvidence=document_evidence,
                      updatedAt=_now())
        if existing:
            for key, value in values.items():
                if key not in {"ownerId", "projectId", "sourceMessageId", "requestOrdinal"}:
                    setattr(existing, key, value)
            await existing.save()
        else:
            await ClientRequest(**values).insert()
        run.status, run.completedAt, run.updatedAt = "COMPLETED", _now(), _now()
        await run.save()
    except Exception:
        run.status, run.errorCode, run.completedAt, run.updatedAt = "FAILED", "ANALYSIS_FAILED", _now(), _now()
        await run.save()


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
        name = material.fileName.lower()
        kind: DocumentType
        if has_api_key() and material.extractedText:
            kind = (
                await _llm_json(
                    [
                        {"role": "system", "content": PROJECT_MATERIAL_SYSTEM_PROMPT},
                        {"role": "user", "content": f"파일명: {material.fileName}\n텍스트: {material.extractedText[:8000]}"},
                    ],
                    MaterialClassificationResult,
                )
            ).documentType
        elif "계약" in name or "contract" in name:
            kind = "CONTRACT"
        elif "제안" in name or "proposal" in name:
            kind = "PROPOSAL"
        elif "요구" in name or "requirement" in name:
            kind = "REQUIREMENTS"
        elif "회의" in name or "meeting" in name:
            kind = "MEETING_NOTES"
        else:
            kind = "OTHER"
        material.documentType, material.classificationStatus, material.updatedAt = kind, "COMPLETED", _now()
        await material.save()
        run.status, run.completedAt, run.updatedAt = "COMPLETED", _now(), _now()
        await run.save()
    except Exception:
        material.classificationStatus, material.updatedAt = "FAILED", _now()
        await material.save()
        run.status, run.errorCode, run.completedAt, run.updatedAt = "FAILED", "CLASSIFICATION_FAILED", _now(), _now()
        await run.save()


@router.post("/projects/{project_id}/source-links/{source_link_id}/sync")
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
    try:
        if link.sourceChannel == "GMAIL":
            connection = await latest_gmail_connection(str(current_user.id))
            if connection is None:
                return fail("Gmail이 연결되어 있지 않습니다.", 404)
            connection, token = await _gmail_connection_token(connection)
            emails = await fetch_recent(access_token=token, max_messages=100)
            if link.counterpartyEmail:
                emails = [e for e in emails if e.from_.address.lower() == link.counterpartyEmail.lower() or any(r.address.lower() == link.counterpartyEmail.lower() for r in e.to)]
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
                        try:
                            await material.insert()
                        except DuplicateKeyError:
                            material = await ProjectMaterial.find_one(
                                ProjectMaterial.ownerId == current_user.id,
                                ProjectMaterial.connectionId == connection.externalId,
                                ProjectMaterial.providerFileId == file.fileId,
                            )
                        if material:
                            material_run = await _ensure_material_run(material)
                            background_tasks.add_task(classify_material_run, str(material_run.id))
    except (IntegrationError, TokenEncryptionError, RuntimeError, ValueError):
        return fail("외부 채널에서 데이터를 가져오지 못했습니다. 연결 상태를 확인해 주세요.", 502)
    return ok({"sourceMessageCount": new_count, "newMessageCount": new_count, "analysisRunIds": run_ids})


@router.get("/analysis-runs/{analysis_run_id}")
async def analysis_run(analysis_run_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    run = await AnalysisRun.find_one(AnalysisRun.id == analysis_run_id, AnalysisRun.ownerId == current_user.id)
    if run is None:
        return fail("분석 실행을 찾을 수 없습니다.", 404)
    return ok(run.model_dump(mode="json", exclude={"id", "ownerId"}) | {"analysisRunId": str(run.id)})


@router.get("/projects/{project_id}/requirements")
async def project_requirements(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    requirements = await Requirement.find(Requirement.ownerId == current_user.id, Requirement.projectId == project.id).to_list()
    from app.public_data import public_requirement
    return ok([public_requirement(item) for item in requirements])


@router.get("/projects/{project_id}/contract")
async def project_contract(project_id: PydanticObjectId, current_user: User | None = Depends(get_current_user)):
    project, error = await _project_or_404(project_id, current_user)
    if error:
        return error
    contract = await Contract.find(Contract.ownerId == current_user.id, Contract.projectId == project.id).sort(-Contract.version).first_or_none()
    if contract is None:
        return fail("등록된 계약이 없습니다.", 404)
    from app.public_data import public_contract
    return ok(public_contract(contract))


@router.post("/projects/{project_id}/contract")
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


@router.post("/projects/{project_id}/contract/apply")
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
        return fail("확인 질문을 만들지 못했습니다. 다시 시도해 주세요.", 502)
    return ok({"questions": questions})


@router.post("/projects/{project_id}/requirements/{requirement_id}/reply")
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
        )
    except Exception:
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
    from core.state_machine import TRANSITIONS
    return ok({"allowed": list(TRANSITIONS[requirement.status])})


@router.post("/projects/{project_id}/requirements/{requirement_id}/transition")
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
