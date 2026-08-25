"""GitHub 저장소 접근 토큰 등록.

Gmail·Slack은 OAuth로 연결하지만 GitHub은 사용자가 PAT를 직접 붙여넣는다.
시연 범위에서 OAuth App을 새로 등록·심사받는 비용이 크고, 저장·사용 방식은
어차피 같기 때문이다(Fernet 암호화 후 서버에서만 복호화).

서버 공용 토큰 하나로 두지 않는 이유는 명확하다. 사람마다 접근 권한이 다른
저장소를 봐야 하므로 토큰이 사용자에게 묶여야 한다. 등록하지 않으면 서버
기본 GITHUB_TOKEN으로 떨어지고, 그마저 없으면 공개 저장소만 볼 수 있다.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.integration_store import github_connection, save_github_connection
from app.response import fail, ok
from infra.security.provider_tokens import TokenEncryptionError
from models.user import User

router = APIRouter(tags=["github"])


class GithubConnectRequest(BaseModel):
    accountName: str = Field(min_length=1, max_length=100)
    personalAccessToken: str = Field(min_length=1, max_length=500)


@router.get("/github/status")
async def github_status(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    connection = await github_connection(str(current_user.id))
    return ok(
        {
            "connected": connection is not None,
            "accountName": connection.externalName if connection else None,
        }
    )


@router.post("/github/connect")
async def github_connect(
    body: GithubConnectRequest,
    current_user: User | None = Depends(get_current_user),
):
    if current_user is None:
        return fail("로그인이 필요합니다.", 401)
    try:
        connection = await save_github_connection(
            owner_id=str(current_user.id),
            account_name=body.accountName,
            personal_access_token=body.personalAccessToken,
        )
    except TokenEncryptionError:
        return fail("토큰을 저장하지 못했습니다. 다시 시도해 주세요.", 500)
    # 토큰 자체는 응답에 넣지 않는다.
    return ok({"connected": True, "accountName": connection.externalName})
