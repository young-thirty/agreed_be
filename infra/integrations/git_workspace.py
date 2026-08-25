"""Git 저장소를 요청 하나짜리 임시 워크스페이스에 얕게 clone하고 정리한다.

shookie(사내 Slack 봇)의 code-explorer 서브 에이전트와 같은 패턴이다. 클론을
캐시해 재사용하지 않고 요청마다 새로 받고 끝나면 지운다 — 항상 최신이고,
워크스페이스가 서버 디스크에 쌓이지 않는다.

GITHUB_TOKEN이 있으면 비공개 저장소도 clone할 수 있고, 없으면 공개 저장소만
된다. 토큰을 clone URL에 넣으므로 로그에 URL을 그대로 남기지 않는다.
"""

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

CLONE_TIMEOUT_SECONDS = 25


class GitWorkspaceError(Exception):
    """사용자가 그대로 읽을 수 있는 문장만 담는다."""


def _clone_url(repo_full_name: str) -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return f"https://x-access-token:{token}@github.com/{repo_full_name}.git"
    return f"https://github.com/{repo_full_name}.git"


@asynccontextmanager
async def cloned_repo(repo_full_name: str) -> AsyncIterator[str]:
    """``async with cloned_repo("owner/repo") as path:`` 형태로 쓴다.

    블록을 벗어나면(예외가 나도) 워크스페이스를 지운다.
    """
    workspace = tempfile.mkdtemp(prefix="agreed-git-")
    try:
        process = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth", "1", "--quiet",
            _clone_url(repo_full_name), workspace,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=CLONE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            process.kill()
            raise GitWorkspaceError("저장소를 가져오는 데 시간이 너무 걸립니다.")
        if process.returncode != 0:
            raise GitWorkspaceError(
                "저장소를 가져오지 못했습니다. 이름(owner/repo)과 접근 권한을 확인해 주세요."
            )
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
