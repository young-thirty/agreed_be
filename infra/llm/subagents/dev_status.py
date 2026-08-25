"""개발 현황 확인 에이전트.

연결된 저장소를 읽기 전용으로 clone해 "이 요청이 건드릴 기능이 지금 어디까지
되어 있는가"를 구조화해 돌려준다. `/git/ask`가 자유 질문에 문장으로 답하는
반면, 여기는 솔루션 파이프라인이 쓸 수 있게 필드로 답한다.

도구는 git_explore와 같은 것을 쓴다(list_files/read_file/grep). 프롬프트와
출력 스키마만 다르다. 도구를 두 벌 만들면 경로 escape 방어도 두 벌이 된다.
"""

from beanie import PydanticObjectId

from core.project_data import DevelopmentStatus
from infra.integrations.git_workspace import GitWorkspaceError, cloned_repo
from infra.llm.client import has_api_key
from infra.llm.harness import run_agent
from infra.llm.prompts import DEVELOPMENT_STATUS_SYSTEM_PROMPT
from infra.llm.schemas import DevelopmentStatusResult
from infra.llm.subagents.git_explore import _build_tools
from infra.security.provider_tokens import (
    TokenEncryptionError, decrypt_provider_token,
)
from models.integration import IntegrationConnection
from models.source_link import ProjectSourceLink


async def _repo_for_project(
    owner_id: PydanticObjectId, project_id: PydanticObjectId
) -> tuple[str | None, str | None]:
    """프로젝트에 연결된 저장소와, 그 소유자의 GitHub 토큰을 찾는다."""
    link = await ProjectSourceLink.find_one(
        ProjectSourceLink.ownerId == owner_id,
        ProjectSourceLink.projectId == project_id,
        ProjectSourceLink.sourceChannel == "GITHUB",
    )
    if link is None or not link.repoFullName:
        return None, None

    # 사용자가 등록한 PAT를 먼저 쓰고 없으면 서버 기본 토큰으로 떨어진다.
    connection = (
        await IntegrationConnection.find(
            IntegrationConnection.ownerId == str(owner_id),
            IntegrationConnection.provider == "github",
        )
        .sort(-IntegrationConnection.updatedAt)
        .first_or_none()
    )
    if connection is None:
        return link.repoFullName, None
    try:
        token = decrypt_provider_token(connection.accessTokenEncrypted)
    except TokenEncryptionError:
        return link.repoFullName, None
    return link.repoFullName, token


async def build_development_status(
    *,
    owner_id: PydanticObjectId,
    project_id: PydanticObjectId,
    summary_title: str,
    requirement: str = "",
) -> DevelopmentStatus | None:
    """저장소가 연결되어 있으면 현재 구현 상태를, 아니면 None을 돌려준다.

    None은 "저장소가 없다"는 뜻이고, 뒤따르는 영향 분석·작업 가능 여부는 그
    사실을 알고 근거 없이 단정하지 않는다.
    """

    if not has_api_key():
        return None

    repo, token = await _repo_for_project(owner_id, project_id)
    if repo is None:
        return None

    task = (
        f"## 클라이언트 요청\n{summary_title}\n"
        f"{requirement}\n\n"
        f"저장소: {repo}\n"
        "이 요청이 건드릴 기능이 지금 어디까지 구현되어 있는지 확인해라."
    )

    try:
        async with cloned_repo(repo, token) as workspace:
            result = await run_agent(
                system_prompt=DEVELOPMENT_STATUS_SYSTEM_PROMPT,
                task=task,
                tools=_build_tools(workspace),
                schema=DevelopmentStatusResult,
            )
    except GitWorkspaceError:
        return None
    except Exception:
        # 저장소를 못 읽어도 나머지 분석은 계속되어야 한다.
        return None

    if result is None:
        return None
    return DevelopmentStatus(
        targetFeature=result.targetFeature,
        currentState=result.currentState,
        relatedPaths=result.relatedPaths,
        relatedRefs=result.relatedRefs,
    )
