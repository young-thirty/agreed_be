"""Beanie 내부 필드를 제외한 프론트 공개 응답 변환."""

from core.domain import ContractState, RequirementState
from models.contract import Contract
from models.requirement import Requirement
from models.project import Project
from models.client_request import ClientRequest, public_client_request
from models.project_material import ProjectMaterial


def public_contract(contract: Contract) -> dict[str, object]:
    data = {
        "id": str(contract.id),
        **contract.model_dump(
            mode="json",
            include=set(ContractState.model_fields),
        ),
    }
    if contract.projectId is not None:
        data["projectId"] = str(contract.projectId)
    return data


def public_requirement(requirement: Requirement) -> dict[str, object]:
    data = {
        "id": str(requirement.id),
        **requirement.model_dump(
            mode="json",
            include=set(RequirementState.model_fields),
        ),
    }
    if requirement.projectId is not None:
        data["projectId"] = str(requirement.projectId)
    if requirement.sourceRequestId is not None:
        data["sourceRequestId"] = str(requirement.sourceRequestId)
    return data


def public_project(project: Project, unanswered_request_count: int = 0) -> dict[str, object]:
    return {
        "projectId": str(project.id),
        "name": project.name,
        "clientName": project.clientName,
        "clientEmail": project.clientEmail,
        "description": project.description,
        "startDate": project.startDate.isoformat() if project.startDate else None,
        "endDate": project.endDate.isoformat() if project.endDate else None,
        "contractPrice": project.contractPrice,
        "unansweredRequestCount": unanswered_request_count,
        "createdAt": project.createdAt.isoformat() + ("Z" if project.createdAt.tzinfo is None else ""),
        "updatedAt": project.updatedAt.isoformat() + ("Z" if project.updatedAt.tzinfo is None else ""),
        "status": project.status,
    }


def public_material(material: ProjectMaterial) -> dict[str, object]:
    # S3에 이미 올려둔 원본이 있으면 그걸 쓰고, Gmail 첨부는 없어도 그 자리에서
    # 다시 받아올 수 있다(GET .../materials/{id}/file이 두 경로를 다 안다).
    # 그래서 storageKey 유무만으로 '읽을 수 있는지'를 판단하면 Gmail 자료를
    # 실제로는 되는데도 안 되는 것처럼 흐리게 보여주게 된다.
    can_fetch_live = (
        material.sourceChannel == "GMAIL"
        and material.connectionId is not None
        and material.providerFileId is not None
        and ":" in material.providerFileId
    )
    return {
        "materialId": str(material.id),
        "projectId": str(material.projectId),
        "fileName": material.fileName,
        "direction": material.direction,
        "communicatedAt": material.communicatedAt.isoformat() + ("Z" if material.communicatedAt.tzinfo is None else ""),
        "classificationStatus": material.classificationStatus,
        "documentType": material.documentType,
        "sourceChannel": material.sourceChannel,
        "mimeType": material.mimeType,
        "sizeBytes": material.sizeBytes,
        "conversationTitle": material.conversationTitle,
        "senderDisplay": material.senderDisplay,
        "hasFile": material.storageKey is not None or can_fetch_live,
    }
