"""Git 코드 탐색 서브 에이전트.

레포를 질문마다 얕게 clone해 임시 워크스페이스를 만들고, 그 안에서만 동작하는
읽기 전용 도구(list_files/read_file/grep)로 답한다. 계약 대조 에이전트와 같은
하네스(infra/llm/harness.py)를 쓴다 — 도구를 스스로 골라 확신이 설 때까지
반복 호출하는 Agentic RAG 패턴이다. 벡터 인덱스를 두지 않는 이유는 어떤
파일을 봐야 할지 미리 알 수 없어서다.

코드를 고치거나 커밋·PR을 만들지 않는다. 질문에 답하는 것까지만 한다.
"""

import re
from pathlib import Path

from infra.integrations.git_workspace import GitWorkspaceError, cloned_repo
from infra.llm.harness import AgentTool, run_agent
from infra.llm.prompts import GIT_EXPLORE_SYSTEM_PROMPT
from infra.llm.schemas import GitExploreResult

MAX_FILES_LISTED = 200
MAX_READ_CHARS = 6000
MAX_GREP_MATCHES = 30


def _safe_path(root: str, relative: str) -> Path | None:
    """워크스페이스 밖으로 나가는 경로를 막는다.

    임의 도구 호출 인자로 ``../../etc/passwd`` 같은 값이 들어올 수 있으므로,
    다른 검증을 다 느슨하게 두더라도 이 escape 하나는 막아야 컨테이너의
    다른 파일이 새어나가지 않는다.
    """
    try:
        target = (Path(root) / relative).resolve()
    except (OSError, ValueError):
        return None
    root_path = Path(root).resolve()
    if target != root_path and root_path not in target.parents:
        return None
    return target


def _list_files(root: str, sub_path: str) -> str:
    base = _safe_path(root, sub_path or ".")
    if base is None or not base.exists():
        return "경로를 찾을 수 없습니다."
    entries: list[str] = []
    for path in sorted(base.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_file():
            entries.append(str(path.relative_to(root)))
        if len(entries) >= MAX_FILES_LISTED:
            entries.append("... (생략)")
            break
    return "\n".join(entries) if entries else "파일이 없습니다."


def _read_file(root: str, path: str) -> str:
    target = _safe_path(root, path)
    if target is None or not target.is_file():
        return "파일을 찾을 수 없습니다."
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "파일을 읽지 못했습니다."
    return text[:MAX_READ_CHARS]


def _grep(root: str, pattern: str) -> str:
    try:
        regex = re.compile(pattern)
    except re.error:
        return "정규식이 올바르지 않습니다."
    matches: list[str] = []
    for path in Path(root).rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{path.relative_to(root)}:{line_no}: {line.strip()[:200]}")
                if len(matches) >= MAX_GREP_MATCHES:
                    return "\n".join(matches)
    return "\n".join(matches) if matches else "일치하는 결과가 없습니다."


def _build_tools(workspace: str) -> list[AgentTool]:
    async def list_files_tool(arguments: dict) -> str:
        return _list_files(workspace, str(arguments.get("path", ".")))

    async def read_file_tool(arguments: dict) -> str:
        return _read_file(workspace, str(arguments.get("path", "")))

    async def grep_tool(arguments: dict) -> str:
        return _grep(workspace, str(arguments.get("pattern", "")))

    return [
        AgentTool(
            name="list_files",
            description="워크스페이스의 파일 목록을 본다. path를 생략하면 루트부터 본다.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "하위 디렉토리 상대 경로"}},
            },
            run=list_files_tool,
        ),
        AgentTool(
            name="read_file",
            description="파일 하나의 내용을 읽는다.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string", "description": "레포 루트 기준 상대 경로"}},
                "required": ["path"],
            },
            run=read_file_tool,
        ),
        AgentTool(
            name="grep",
            description="정규식으로 저장소 전체 파일 내용을 검색한다.",
            parameters={
                "type": "object",
                "properties": {"pattern": {"type": "string", "description": "검색할 정규식"}},
                "required": ["pattern"],
            },
            run=grep_tool,
        ),
    ]


async def ask_repository(*, repo_full_name: str, question: str) -> str:
    """레포에서 질문에 답한다. 클론·탐색·정리를 한 호출 안에서 마친다."""
    try:
        async with cloned_repo(repo_full_name) as workspace:
            result = await run_agent(
                system_prompt=GIT_EXPLORE_SYSTEM_PROMPT,
                task=f"저장소: {repo_full_name}\n질문: {question}",
                tools=_build_tools(workspace),
                schema=GitExploreResult,
            )
    except GitWorkspaceError as error:
        return str(error)

    if result is None:
        return "저장소에서 답을 찾지 못했습니다."
    return result.answer
