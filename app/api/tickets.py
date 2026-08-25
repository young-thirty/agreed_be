"""최신 프론트 프로토타입이 쓰는 티켓 중심 조회·판단 API."""

from datetime import datetime
from typing import Any, Literal

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.public_data import public_material, public_project
from app.response import fail, ok
from core.project_data import TicketCategory, TicketHandling
from models import ClientRequest, Project, ProjectMaterial, SourceMessage, TicketDecision
from models.client_request import ticket_code
from models.user import User

router = APIRouter()


def _now() -> datetime:
    return datetime.utcnow()


def _iso(value: datetime) -> str:
    return value.isoformat() + ("Z" if value.tzinfo is None else "")


def _channel(value: str) -> Literal["email", "slack"]:
    return "slack" if value == "SLACK" else "email"


def _ticket_status(value: str) -> Literal["Active", "Done", "Reject"]:
    return {"active": "Active", "done": "Done", "rejected": "Reject"}[value]


class TicketProposal(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    category: TicketCategory = "일반 질문"
    requirement: str = Field(default="", max_length=500)
    summary: str = Field(default="", max_length=1000)


class DecisionRequest(BaseModel):
    sourceMessageId: PydanticObjectId
    handling: TicketHandling | None = None
    values: dict[str, str] = Field(default_factory=dict)
    ticketProposal: TicketProposal | None = None


class MarkSentRequest(BaseModel):
    sourceMessageId: PydanticObjectId
    replyText: str = Field(min_length=1, max_length=5000)


async def _owned_ticket(ticket_id: PydanticObjectId, owner_id: PydanticObjectId):
    return await ClientRequest.find_one(
        ClientRequest.id == ticket_id, ClientRequest.ownerId == owner_id
    )


async def _next_ticket_code(owner_id: PydanticObjectId) -> str:
    tickets = await ClientRequest.find(ClientRequest.ownerId == owner_id).to_list()
    numbers: list[int] = []
    for ticket in tickets:
        if ticket.ticketCode and ticket.ticketCode.startswith("TCK-"):
            suffix = ticket.ticketCode[4:]
            if suffix.isdigit():
                numbers.append(int(suffix))
    return f"TCK-{max(numbers, default=0) + 1:02d}"


async def _next_request_ordinal(owner_id: PydanticObjectId, source_id: PydanticObjectId) -> int:
    siblings = await ClientRequest.find(
        ClientRequest.ownerId == owner_id,
        ClientRequest.sourceMessageId == source_id,
    ).to_list()
    return max((item.requestOrdinal for item in siblings), default=-1) + 1


async def _remove_created_target(
    decision: TicketDecision | None, original_ticket_id: PydanticObjectId,
) -> None:
    """판단을 바꿀 때 이 판단이 만든 임시 티켓만 정리한다."""

    if (
        decision is None or decision.handling != "create"
        or decision.targetTicketId is None or decision.targetTicketId == original_ticket_id
    ):
        return
    created = await ClientRequest.find_one(
        ClientRequest.id == decision.targetTicketId,
        ClientRequest.ownerId == decision.ownerId,
        ClientRequest.sourceMessageId == decision.sourceMessageId,
    )
    if created:
        await created.delete()


def _decision_payload(decision: TicketDecision | None) -> dict[str, Any]:
    if decision is None:
        return {
            "handling": None, "ticketId": None, "values": {},
            "replyText": None, "sentAt": None,
        }
    return {
        "handling": decision.handling,
        "ticketId": str(decision.targetTicketId) if decision.targetTicketId else None,
        "values": decision.values,
        "replyText": decision.replyText,
        "sentAt": _iso(decision.sentAt) if decision.sentAt else None,
    }


def _analysis_payload(ticket: ClientRequest, decision: TicketDecision | None) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    if ticket.aiDecisionStatus:
        decision_label = {
            "IN_SCOPE_ACTION_REQUIRED": "계약 범위 안",
            "OUT_OF_SCOPE_COORDINATION_REQUIRED": "확인·조율 필요",
            "EXTRA_REQUEST": "계약 범위 밖",
        }[ticket.aiDecisionStatus]
        fields.append({"label": "계약 판단", "value": decision_label,
                       "tone": "neutral" if ticket.aiDecisionStatus == "IN_SCOPE_ACTION_REQUIRED" else "caution"})
    if ticket.decisionReason:
        fields.append({"label": "판단 근거", "value": ticket.decisionReason})

    evidence = [
        {"source": "message", "label": "고객 메시지", "title": "요청 원문", "quote": item.quote}
        for item in ticket.requestEvidence
    ]
    evidence.extend(
        {"source": "document", "label": "프로젝트 자료", "title": item.documentId, "quote": item.quote}
        for item in ticket.documentEvidence
    )
    drafts = {"base": "", "friendly": "", "short": "", "firm": ""}
    if decision:
        drafts.update({key: value for key, value in decision.drafts.items() if key in drafts})
    decision_fields = []
    if ticket.aiDecisionStatus == "EXTRA_REQUEST":
        decision_fields = [
            {"id": "amount", "label": "추가 비용", "type": "money", "placeholder": "예: 300000"},
            {"id": "dueDate", "label": "완료 예정일", "type": "date"},
        ]
    return {
        "headline": ticket.currentSummary or ticket.decisionReason or ticket.summaryTitle or "요청을 확인해 주세요.",
        "intents": [],
        "fields": fields,
        "missingInfo": [],
        "devContext": None,
        "evidence": evidence,
        "relatedTicketId": str(ticket.id),
        "ticketProposal": {
            "title": ticket.summaryTitle or "새 고객 요청",
            "category": ticket.category,
            "requirement": ticket.requirement,
            "summary": ticket.currentSummary or ticket.decisionReason or "",
        },
        "decisionFields": decision_fields,
        "drafts": drafts,
    }


def _inbound_payload(
    message: SourceMessage, ticket: ClientRequest, decision: TicketDecision | None,
) -> dict[str, Any]:
    body = message.rawText.strip()
    preview = next((line.strip() for line in body.splitlines() if line.strip()), body)[:160]
    sender_external = message.senderExternalId or ""
    return {
        "inboundId": str(message.id),
        "channel": _channel(message.sourceChannel),
        "projectId": str(message.projectId),
        "ticketId": str(ticket.id),
        "fromName": message.senderDisplay or "",
        "fromEmail": sender_external if "@" in sender_external else "",
        "subject": message.conversationDisplay or "",
        "preview": preview,
        "body": body,
        "attachments": message.attachmentRefs,
        "createdAt": _iso(message.occurredAt),
        "initialStage": "to_analyze" if ticket.aiProcessingStatus != "COMPLETED" else "to_reply",
        "category": ticket.category,
        "analysis": _analysis_payload(ticket, decision),
    }


def _ticket_payload(ticket: ClientRequest, last_message: SourceMessage | None) -> dict[str, Any]:
    return {
        "ticketId": str(ticket.id),
        "ticketCode": ticket_code(ticket),
        "projectId": str(ticket.projectId),
        "title": ticket.summaryTitle or "제목 없는 요청",
        "summary": ticket.currentSummary or ticket.decisionReason or "",
        "status": _ticket_status(ticket.ticketStatus),
        "category": ticket.category,
        "requirement": ticket.requirement,
        "lastCustomerMessage": last_message.rawText.strip() if last_message else None,
        "createdAt": _iso(ticket.createdAt),
        "updatedAt": _iso(ticket.updatedAt),
    }


async def _routed_messages(
    ticket: ClientRequest, owner_id: PydanticObjectId, decisions: list[TicketDecision],
) -> list[SourceMessage]:
    source_ids = set(ticket.sourceMessageIds or [ticket.sourceMessageId])
    source_ids.add(ticket.sourceMessageId)
    for decision in decisions:
        if decision.targetTicketId == ticket.id:
            source_ids.add(decision.sourceMessageId)
        elif decision.requestId == ticket.id and decision.handling == "create":
            source_ids.discard(decision.sourceMessageId)
    if not source_ids:
        return []
    return await SourceMessage.find(
        SourceMessage.ownerId == owner_id,
        {"_id": {"$in": list(source_ids)}},
    ).sort(SourceMessage.occurredAt).to_list()


def _decision_for(
    ticket: ClientRequest, source_id: PydanticObjectId, decisions: list[TicketDecision],
) -> TicketDecision | None:
    return next((
        item for item in decisions
        if item.sourceMessageId == source_id
        and (item.requestId == ticket.id or item.targetTicketId == ticket.id)
    ), None)


async def _work_item(
    ticket: ClientRequest, owner_id: PydanticObjectId, decisions: list[TicketDecision],
) -> dict[str, Any]:
    messages = await _routed_messages(ticket, owner_id, decisions)
    received = [item for item in messages if item.direction == "RECEIVED"]
    last_message = received[-1] if received else None
    pending_message = next((
        item for item in reversed(received)
        if (_decision_for(ticket, item.id, decisions) is None
            or _decision_for(ticket, item.id, decisions).sentAt is None)
    ), None)
    pending_decision = (
        _decision_for(ticket, pending_message.id, decisions) if pending_message else None
    )
    if pending_message:
        stage = "to_analyze" if ticket.aiProcessingStatus != "COMPLETED" else "to_reply"
    elif any(item.sentAt for item in decisions if item.requestId == ticket.id or item.targetTicketId == ticket.id):
        stage = "waiting"
    else:
        stage = "idle"
    activity_dates = [ticket.updatedAt, *[item.occurredAt for item in messages]]
    return {
        "ticket": _ticket_payload(ticket, last_message),
        "pending": _inbound_payload(pending_message, ticket, pending_decision) if pending_message else None,
        "lastActivityAt": _iso(max(activity_dates)),
        "workStage": stage,
    }


@router.get("/tickets", tags=["request"])
async def list_tickets(
    projectId: PydanticObjectId | None = None,
    status: Literal["Active", "Done", "Reject"] | None = None,
    current_user: User | None = Depends(get_current_user),
):
    """티켓 목록 화면에 필요한 티켓·미답변 원문·현재 단계를 한 번에 준다."""
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    query: dict[str, Any] = {"ownerId": current_user.id}
    if projectId:
        query["projectId"] = projectId
    if status:
        query["ticketStatus"] = {"Active": "active", "Done": "done", "Reject": "rejected"}[status]
    tickets = await ClientRequest.find(query).sort(-ClientRequest.updatedAt).to_list()
    decisions = await TicketDecision.find(TicketDecision.ownerId == current_user.id).to_list()
    return ok([await _work_item(ticket, current_user.id, decisions) for ticket in tickets])


@router.get("/tickets/{ticket_id}", tags=["request"])
async def ticket_detail(
    ticket_id: PydanticObjectId, current_user: User | None = Depends(get_current_user),
):
    """프로토타입 상세 화면 한 장에 필요한 Mongo 문서를 합쳐서 준다."""
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    ticket = await _owned_ticket(ticket_id, current_user.id)
    if ticket is None:
        return fail("티켓을 찾을 수 없습니다.", 404)
    project = await Project.find_one(Project.id == ticket.projectId, Project.ownerId == current_user.id)
    if project is None:
        return fail("프로젝트를 찾을 수 없습니다.", 404)
    decisions = await TicketDecision.find(TicketDecision.ownerId == current_user.id).to_list()
    messages = await _routed_messages(ticket, current_user.id, decisions)
    work_item = await _work_item(ticket, current_user.id, decisions)
    history: list[dict[str, Any]] = []
    for message in messages:
        decision = _decision_for(ticket, message.id, decisions)
        if message.direction == "RECEIVED":
            history.append({"kind": "in", "at": _iso(message.occurredAt),
                            "inbound": _inbound_payload(message, ticket, decision)})
        if decision and decision.sentAt and decision.replyText:
            history.append({
                "kind": "out", "at": _iso(decision.sentAt),
                "outbound": {
                    "outboundId": str(decision.id), "channel": _channel(message.sourceChannel),
                    "projectId": str(ticket.projectId), "ticketId": str(ticket.id),
                    "toEmail": message.senderExternalId or "", "body": decision.replyText,
                    "createdAt": _iso(decision.sentAt),
                },
            })
    history.sort(key=lambda item: item["at"])
    materials = await ProjectMaterial.find(
        ProjectMaterial.ownerId == current_user.id,
        ProjectMaterial.projectId == ticket.projectId,
        {"$or": [{"ticketId": ticket.id}, {"ticketId": None}]},
    ).sort(-ProjectMaterial.communicatedAt).to_list()
    pending = work_item["pending"]
    current_decision = None
    if pending:
        current_decision = _decision_for(ticket, PydanticObjectId(pending["inboundId"]), decisions)
    return ok({
        **work_item,
        "project": public_project(project),
        "decision": _decision_payload(current_decision),
        "history": history,
        "materials": [public_material(item) for item in materials],
        # 저장된 솔루션만 실어 보낸다. 여기서 만들지 않는 이유는 상세 조회가
        # 에이전트 여러 개를 기다리는 느린 API가 되면 안 되기 때문이다.
        # 만드는 것은 POST /api/requests/{id}/solution 이 맡는다.
        "solution": ticket.solution.model_dump(mode="json") if ticket.solution else None,
    })


@router.post("/requests/{request_id}/decision", tags=["request"])
async def save_decision(
    request_id: PydanticObjectId, body: DecisionRequest,
    current_user: User | None = Depends(get_current_user),
):
    """localStorage에 있던 처리 방식과 확정 입력값을 MongoDB에 저장한다."""
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    ticket = await _owned_ticket(request_id, current_user.id)
    if ticket is None:
        return fail("티켓을 찾을 수 없습니다.", 404)
    message = await SourceMessage.find_one(
        SourceMessage.id == body.sourceMessageId,
        SourceMessage.ownerId == current_user.id,
        SourceMessage.projectId == ticket.projectId,
    )
    if message is None:
        return fail("고객 메시지를 찾을 수 없습니다.", 404)
    if len(body.values) > 20 or any(
        len(key) > 80 or len(value) > 1000 for key, value in body.values.items()
    ):
        return fail("판단 입력값이 너무 깁니다.", 400)
    existing = await TicketDecision.find_one(
        TicketDecision.ownerId == current_user.id,
        TicketDecision.requestId == ticket.id,
        TicketDecision.sourceMessageId == message.id,
    )
    if existing and existing.sentAt:
        return fail("이미 발송 완료한 판단은 바꿀 수 없습니다.", 409)
    if body.handling is None:
        if existing:
            await _remove_created_target(existing, ticket.id)
            await existing.delete()
        return ok(_decision_payload(None))

    target = ticket
    if body.handling == "create":
        proposal = body.ticketProposal
        if proposal is None:
            return fail("새 티켓을 만들 제목과 요구사항이 필요합니다.", 400)
        target = None
        if existing and existing.handling == "create" and existing.targetTicketId:
            target = await _owned_ticket(existing.targetTicketId, current_user.id)
        if target is None:
            target = ClientRequest(
                ownerId=current_user.id, projectId=ticket.projectId,
                sourceMessageId=message.id, sourceMessageIds=[message.id],
                requestOrdinal=await _next_request_ordinal(current_user.id, message.id),
                ticketCode=await _next_ticket_code(current_user.id),
                sourceChannel=message.sourceChannel, senderDisplay=message.senderDisplay,
                occurredAt=message.occurredAt, aiProcessingStatus="COMPLETED",
                summaryTitle=proposal.title, aiDecisionStatus=ticket.aiDecisionStatus,
                decisionReason=ticket.decisionReason, category=proposal.category,
                requirement=proposal.requirement, currentSummary=proposal.summary,
                requestEvidence=ticket.requestEvidence, documentEvidence=ticket.documentEvidence,
            )
            await target.insert()
        else:
            target.summaryTitle = proposal.title
            target.category = proposal.category
            target.requirement = proposal.requirement
            target.currentSummary = proposal.summary
            target.updatedAt = _now()
            await target.save()
    elif body.handling == "ignore":
        await _remove_created_target(existing, ticket.id)
        target = None
    elif existing and existing.handling == "create":
        await _remove_created_target(existing, ticket.id)

    if existing is None:
        existing = TicketDecision(
            ownerId=current_user.id, projectId=ticket.projectId,
            requestId=ticket.id, sourceMessageId=message.id,
            handling=body.handling, targetTicketId=target.id if target else None,
            values=body.values,
        )
        await existing.insert()
    else:
        existing.handling = body.handling
        existing.targetTicketId = target.id if target else None
        existing.values = body.values
        existing.updatedAt = _now()
        await existing.save()
    return ok(_decision_payload(existing))


@router.post("/requests/{request_id}/mark-sent", tags=["request"])
async def mark_sent(
    request_id: PydanticObjectId, body: MarkSentRequest,
    current_user: User | None = Depends(get_current_user),
):
    """시연 화면에서 답변을 보낸 것으로 표시한다. 외부 채널 발송 API는 아니다."""
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    ticket = await _owned_ticket(request_id, current_user.id)
    if ticket is None:
        return fail("티켓을 찾을 수 없습니다.", 404)
    decision = await TicketDecision.find_one(
        TicketDecision.ownerId == current_user.id,
        TicketDecision.requestId == ticket.id,
        TicketDecision.sourceMessageId == body.sourceMessageId,
    )
    if decision is None:
        return fail("먼저 이 메시지의 처리 방식을 저장해 주세요.", 409)
    decision.replyText = body.replyText.strip()
    decision.sentAt = decision.sentAt or _now()
    decision.updatedAt = _now()
    await decision.save()
    return ok(_decision_payload(decision))
