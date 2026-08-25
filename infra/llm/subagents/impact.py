"""영향 분석과 작업 가능 여부 판단.

둘 다 도구를 쥐지 않는다. 저장소를 뒤져야 하는 일은 개발 현황 에이전트가 이미
끝냈고, 여기는 그 결과를 읽기 전용 컨텍스트로 받아 판단만 한다. 도구가 필요
없는 판단에 도구 루프를 돌리면 토큰과 시간만 쓴다.

파일이나 DB를 실제로 고치지 않는다. 무엇을 건드리게 되는지만 적는다.
"""

from core.project_data import DevelopmentStatus, Feasibility, ImpactAnalysis
from infra.llm.harness import run_json
from infra.llm.prompts import (
    FEASIBILITY_SYSTEM_PROMPT,
    IMPACT_ANALYSIS_SYSTEM_PROMPT,
)
from infra.llm.schemas import FeasibilityResult, ImpactAnalysisResult


def _status_block(status: DevelopmentStatus | None) -> str:
    """개발 현황을 프롬프트에 넣을 형태로 만든다.

    저장소가 연결되지 않았으면 그 사실을 명시한다. 비워 두면 모델이 코드를
    확인한 것처럼 지어내기 쉽다.
    """
    if status is None:
        return "(연결된 저장소가 없어 현재 구현 상태를 확인하지 못했다)"
    lines = [
        f"- 대상 기능: {status.targetFeature or '확인 못 함'}",
        f"- 현재 상태: {status.currentState or '확인 못 함'}",
    ]
    if status.relatedPaths:
        lines.append(f"- 관련 파일: {', '.join(status.relatedPaths)}")
    if status.relatedRefs:
        lines.append(f"- 관련 브랜치·PR: {', '.join(status.relatedRefs)}")
    return "\n".join(lines)


def _task(summary_title: str, requirement: str, status: DevelopmentStatus | None) -> str:
    return (
        f"## 클라이언트 요청\n{summary_title}\n{requirement}\n\n"
        f"## 확인된 개발 현황\n{_status_block(status)}"
    )


async def build_impact_analysis(
    *,
    summary_title: str,
    requirement: str = "",
    development_status: DevelopmentStatus | None = None,
) -> ImpactAnalysis | None:
    result = await run_json(
        system_prompt=IMPACT_ANALYSIS_SYSTEM_PROMPT,
        user_content=_task(summary_title, requirement, development_status),
        schema=ImpactAnalysisResult,
    )
    if result is None:
        return None
    return ImpactAnalysis(**result.model_dump())


async def build_feasibility(
    *,
    summary_title: str,
    requirement: str = "",
    development_status: DevelopmentStatus | None = None,
) -> Feasibility:
    """작업 가능 여부. 실패하면 needs_clarification으로 떨어진다.

    모델을 못 불렀다고 feasible이라고 답하면 사람이 잘못된 확신을 갖는다.
    확인하지 못했다는 사실이 남아야 한다.
    """
    result = await run_json(
        system_prompt=FEASIBILITY_SYSTEM_PROMPT,
        user_content=_task(summary_title, requirement, development_status),
        schema=FeasibilityResult,
    )
    if result is None:
        return Feasibility(
            verdict="needs_clarification",
            reason="자동 판단을 하지 못했습니다. 직접 확인이 필요합니다.",
        )
    return Feasibility(**result.model_dump())
