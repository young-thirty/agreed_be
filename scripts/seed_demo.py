"""최신 티켓 프로토타입과 같은 모양의 10분 시연 데이터를 넣는다.

사용법: ``python scripts/seed_demo.py --email demo@agreed.local``
같은 프로젝트·티켓·원문은 재사용하므로 여러 번 실행해도 중복되지 않는다.
"""

import argparse
import asyncio
import hashlib
import os
from datetime import datetime

from beanie import init_beanie
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

from models import (
    ClientRequest, DOCUMENT_MODELS, Project, ProjectMaterial, ProjectSourceLink,
    SourceMessage, TicketDecision, User,
)


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def project_for(user: User, values: dict) -> Project:
    project = await Project.find_one(Project.ownerId == user.id, Project.name == values["name"])
    if project is None:
        project = Project(ownerId=user.id, statusRank=0, **values)
        await project.insert()
    return project


async def gmail_link_for(user: User, project: Project) -> ProjectSourceLink:
    link = await ProjectSourceLink.find_one(
        ProjectSourceLink.ownerId == user.id,
        ProjectSourceLink.projectId == project.id,
        ProjectSourceLink.locatorKey == f"demo:{project.id}:gmail",
    )
    if link is None:
        link = ProjectSourceLink(
            ownerId=user.id, projectId=project.id, connectionId="demo-gmail",
            sourceChannel="GMAIL", displayName=f"{project.clientName} 이메일",
            counterpartyEmail=project.clientEmail,
            locatorKey=f"demo:{project.id}:gmail",
        )
        await link.insert()
    return link


async def message_for(
    user: User, project: Project, link: ProjectSourceLink, *, key: str,
    sender: str, sender_name: str, subject: str, body: str, at: str,
) -> SourceMessage:
    message = await SourceMessage.find_one(
        SourceMessage.ownerId == user.id, SourceMessage.sourceKey == key,
    )
    if message is None:
        message = SourceMessage(
            ownerId=user.id, projectId=project.id, sourceLinkId=link.id,
            connectionId="demo-gmail", sourceChannel="GMAIL", sourceKey=key,
            providerMessageId=key, providerThreadId=f"thread:{key}",
            senderExternalId=sender, senderDisplay=sender_name,
            conversationDisplay=subject, direction="RECEIVED", rawText=body,
            occurredAt=dt(at), contentHash=digest(body),
        )
        await message.insert()
    return message


async def ticket_for(
    user: User, project: Project, message: SourceMessage, *, code: str,
    title: str, summary: str, category: str, requirement: str,
    decision: str, reason: str, status: str = "active",
) -> ClientRequest:
    ticket = await ClientRequest.find_one(
        ClientRequest.ownerId == user.id, ClientRequest.ticketCode == code,
    )
    if ticket is None:
        ticket = ClientRequest(
            ownerId=user.id, projectId=project.id, sourceMessageId=message.id,
            sourceMessageIds=[message.id], requestOrdinal=0, ticketCode=code,
            sourceChannel="GMAIL", senderDisplay=message.senderDisplay,
            occurredAt=message.occurredAt, aiProcessingStatus="COMPLETED",
            summaryTitle=title, aiDecisionStatus=decision, decisionReason=reason,
            category=category, requirement=requirement, currentSummary=summary,
            ticketStatus=status,
            requestEvidence=[{"quote": message.rawText.splitlines()[0],
                              "sourceMessageId": str(message.id)}],
            createdAt=message.occurredAt, updatedAt=message.occurredAt,
        )
        await ticket.insert()
    return ticket


async def material_for(
    user: User, project: Project, *, file_name: str, kind: str, summary: str,
) -> None:
    provider_id = f"demo:{project.id}:{file_name}"
    material = await ProjectMaterial.find_one(
        ProjectMaterial.ownerId == user.id,
        ProjectMaterial.providerFileId == provider_id,
    )
    if material is None:
        await ProjectMaterial(
            ownerId=user.id, projectId=project.id, sourceChannel="GMAIL",
            connectionId="demo-gmail", providerFileId=provider_id,
            fileName=file_name, mimeType="application/pdf", sizeBytes=184320,
            direction="RECEIVED", communicatedAt=dt("2026-08-01T10:00:00"),
            classificationStatus="COMPLETED", documentType=kind, summary=summary,
            contentHash=digest(provider_id),
        ).insert()


async def seed(email: str) -> None:
    load_dotenv()
    client = AsyncMongoClient(os.environ.get("MONGODB_URL", "mongodb://localhost:27017"))
    try:
        await init_beanie(
            database=client[os.environ.get("MONGODB_DB", "agreed")],
            document_models=DOCUMENT_MODELS,
        )
        user = await User.find_one(User.email == email.lower())
        if user is None:
            raise SystemExit("먼저 /api/auth/signup으로 회원가입한 이메일을 사용하세요.")

        cases = [
            {
                "project": dict(
                    name="A사 홈페이지 리뉴얼", clientName="A사",
                    clientEmail="jiwon@acme.co.kr", description="홈페이지 전면 리뉴얼",
                    contractPrice=5_000_000, status="ACTIVE",
                ),
                "message": dict(
                    key="demo-acme-kakao", sender="jiwon@acme.co.kr", sender_name="박지원",
                    subject="로그인 기능 추가 요청", body="카카오 로그인도 추가해주세요.",
                    at="2026-08-26T09:12:00",
                ),
                "ticket": dict(
                    code="TCK-12", title="로그인 기능",
                    summary="이메일 로그인 구현은 완료되었고 카카오 로그인을 추가할지 검토 중입니다.",
                    category="기능 요청", requirement="이메일 기반 회원 로그인 제공 (JWT 인증)",
                    decision="EXTRA_REQUEST", reason="계약서에는 이메일 로그인을 제공한다고 명시되어 있습니다.",
                ),
                "materials": [
                    ("A사_홈페이지_리뉴얼_제안서.pdf", "PROPOSAL", "이메일 로그인과 홈페이지 구현 범위를 정리한 제안서"),
                    ("A사_개발용역_계약서.pdf", "CONTRACT", "프로젝트 범위·금액·납기를 정한 계약서"),
                ],
            },
            {
                "project": dict(
                    name="D사 예약 서비스 구축", clientName="D사",
                    clientEmail="minho@d-booking.kr", description="예약 관리 서비스 구축",
                    contractPrice=7_500_000, status="ACTIVE",
                ),
                "message": dict(
                    key="demo-delta-kakao", sender="minho@d-booking.kr", sender_name="최민호",
                    subject="로그인 관련 문의드립니다",
                    body="카카오 로그인도 추가해주세요.\n이번 주에는 되는 거죠? 기존 견적에 포함된 거죠?",
                    at="2026-08-26T10:02:00",
                ),
                "ticket": dict(
                    code="TCK-91", title="카카오 소셜 로그인 추가",
                    summary="카카오 로그인 추가 비용과 완료 예정일을 정한 뒤 안내해야 합니다.",
                    category="기능 요청", requirement="(미확정) 카카오 OAuth 로그인 추가",
                    decision="EXTRA_REQUEST", reason="기존 계약 범위에 소셜 로그인이 포함되어 있지 않습니다.",
                ),
                "materials": [
                    ("D사_예약서비스_제안서.pdf", "PROPOSAL", "예약 기능과 이메일 인증 범위를 정리한 제안서"),
                    ("D사_계약서_v2.pdf", "CONTRACT", "최종 합의된 개발 범위를 담은 계약서"),
                ],
            },
            {
                "project": dict(
                    name="B사 커머스 앱 개편", clientName="B사",
                    clientEmail="seojun@bstore.kr", description="커머스 앱 사용성 개편",
                    contractPrice=6_000_000, status="ACTIVE",
                ),
                "message": dict(
                    key="demo-beta-login", sender="seojun@bstore.kr", sender_name="김서준",
                    subject="로그인 오류", body="로그인이 안 돼요. 확인 부탁드립니다.",
                    at="2026-08-26T08:05:00",
                ),
                "ticket": dict(
                    code="TCK-56", title="로그인 오류 신고",
                    summary="재현을 위해 기기·브라우저·오류 메시지를 고객에게 요청한 상태입니다.",
                    category="버그", requirement="(확인 중) 로그인 실패 원인 파악 및 수정",
                    decision="OUT_OF_SCOPE_COORDINATION_REQUIRED",
                    reason="원인을 특정하기 위한 재현 정보가 부족합니다.",
                ),
                "materials": [
                    ("B사_앱개편_계약서.pdf", "CONTRACT", "앱 개편의 작업 범위를 정한 계약서"),
                ],
            },
        ]

        seeded_projects: list[Project] = []
        for case in cases:
            project = await project_for(user, case["project"])
            link = await gmail_link_for(user, project)
            message = await message_for(user, project, link, **case["message"])
            ticket = await ticket_for(user, project, message, **case["ticket"])
            for file_name, kind, summary in case["materials"]:
                await material_for(user, project, file_name=file_name, kind=kind, summary=summary)
            seeded_projects.append(project)

            # 한 사례는 이미 답장한 대기 상태로 만들어 목록 단계도 시험한다.
            if case["ticket"]["code"] == "TCK-56":
                decision = await TicketDecision.find_one(
                    TicketDecision.ownerId == user.id,
                    TicketDecision.requestId == ticket.id,
                    TicketDecision.sourceMessageId == message.id,
                )
                if decision is None:
                    await TicketDecision(
                        ownerId=user.id, projectId=project.id, requestId=ticket.id,
                        sourceMessageId=message.id, handling="link", targetTicketId=ticket.id,
                        replyText="확인을 위해 사용 중인 기기와 브라우저, 오류 화면을 보내주세요.",
                        sentAt=dt("2026-08-26T08:15:00"),
                    ).insert()

        print("시연 데이터 seed 완료")
        print("projectIds=" + ",".join(str(item.id) for item in seeded_projects))
        print("GET /api/tickets 에서 최신 프로토타입 DTO를 확인하세요.")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    asyncio.run(seed(args.email))
