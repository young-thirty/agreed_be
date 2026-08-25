"""L4 승인 게이트와 계약 변경분 계산.

시스템 전체에서 계약을 변경하는 통로는 apply_to_contract 하나뿐이다.
"""

from core.domain import ContractDiff, ContractState, RequirementState


def apply_to_contract(
    contract: ContractState,
    requirement: RequirementState,
    requirement_id: str | None = None,
) -> ContractState:
    """합의된 요구사항을 계약에 반영한다.

    status가 '합의'가 아니거나 decision이 없으면 예외를 던진다. 조용히
    무시하지 않고 드러낸다. 이 검사는 호출 위치와 무관하게 함수 자체에 있으므로,
    우회할 다른 경로를 만들지 않는 한 항상 지켜진다.

    인자 타입이 Beanie Document가 아니라 값 객체(ContractState / RequirementState)인
    것에 유의한다. Document는 이 클래스들을 상속하므로 그대로 넘길 수 있고,
    core/는 Beanie를 몰라도 된다.
    """
    if requirement.status != "합의":
        raise ValueError(f"'{requirement.title}'은(는) 아직 합의되지 않았습니다.")
    if requirement.decision is None:
        raise ValueError(f"'{requirement.title}'에 확정된 금액·납기가 없습니다.")

    return ContractState(
        version=contract.version + 1,
        scope=[*contract.scope, requirement.title],
        dueDate=requirement.decision.dueDate,
        amount=contract.amount + requirement.decision.amountDelta,
        appliedRequirementId=requirement_id,
    )


def diff_contract(before: ContractState, after: ContractState) -> ContractDiff:
    """계약 두 버전 사이의 변경분을 계산한다. 화면의 diff 표시가 이 값을 그대로 그린다."""
    before_scope = set(before.scope)
    after_scope = set(after.scope)

    return ContractDiff(
        scopeAdded=[s for s in after.scope if s not in before_scope],
        scopeRemoved=[s for s in before.scope if s not in after_scope],
        dueDateChanged=(
            None
            if before.dueDate == after.dueDate
            else {"before": before.dueDate, "after": after.dueDate}
        ),
        amountDelta=after.amount - before.amount,
    )
