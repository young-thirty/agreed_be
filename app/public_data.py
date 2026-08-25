"""Beanie 내부 필드를 제외한 프론트 공개 응답 변환."""

from core.domain import ContractState, RequirementState
from models.contract import Contract
from models.requirement import Requirement


def public_contract(contract: Contract) -> dict[str, object]:
    return {
        "id": str(contract.id),
        **contract.model_dump(
            mode="json",
            include=set(ContractState.model_fields),
        ),
    }


def public_requirement(requirement: Requirement) -> dict[str, object]:
    return {
        "id": str(requirement.id),
        **requirement.model_dump(
            mode="json",
            include=set(RequirementState.model_fields),
        ),
    }
