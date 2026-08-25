"""사용자별 Gmail·Slack 연결의 영속화 경계."""

from datetime import datetime

from infra.security.provider_tokens import decrypt_provider_token, encrypt_provider_token
from models.integration import IntegrationConnection


def utc_now() -> datetime:
    return datetime.utcnow()


async def save_gmail_connection(
    *,
    owner_id: str,
    email: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime,
    scopes: list[str],
) -> IntegrationConnection:
    connection = await IntegrationConnection.find_one(
        IntegrationConnection.ownerId == owner_id,
        IntegrationConnection.provider == "gmail",
        IntegrationConnection.externalId == email,
    )

    if connection is None:
        if not refresh_token:
            raise ValueError("Google refresh token을 받지 못했습니다. Gmail 연결을 다시 승인해 주세요.")
        connection = IntegrationConnection(
            ownerId=owner_id,
            provider="gmail",
            externalId=email,
            externalName=email,
            accessTokenEncrypted=encrypt_provider_token(access_token),
            refreshTokenEncrypted=encrypt_provider_token(refresh_token),
            accessTokenExpiresAt=expires_at,
            scopes=scopes,
        )
        await connection.insert()
        return connection

    connection.accessTokenEncrypted = encrypt_provider_token(access_token)
    if refresh_token:
        connection.refreshTokenEncrypted = encrypt_provider_token(refresh_token)
    connection.accessTokenExpiresAt = expires_at
    connection.scopes = scopes
    connection.updatedAt = utc_now()
    await connection.save()
    return connection


async def latest_gmail_connection(owner_id: str) -> IntegrationConnection | None:
    return (
        await IntegrationConnection.find(
            IntegrationConnection.ownerId == owner_id,
            IntegrationConnection.provider == "gmail",
        )
        .sort(-IntegrationConnection.updatedAt)
        .first_or_none()
    )


async def save_slack_connection(
    *,
    owner_id: str,
    team_id: str,
    team_name: str,
    bot_token: str,
    scopes: list[str],
) -> IntegrationConnection:
    connection = await IntegrationConnection.find_one(
        IntegrationConnection.ownerId == owner_id,
        IntegrationConnection.provider == "slack",
        IntegrationConnection.externalId == team_id,
    )

    if connection is None:
        connection = IntegrationConnection(
            ownerId=owner_id,
            provider="slack",
            externalId=team_id,
            externalName=team_name,
            accessTokenEncrypted=encrypt_provider_token(bot_token),
            scopes=scopes,
        )
        await connection.insert()
        return connection

    connection.externalName = team_name
    connection.accessTokenEncrypted = encrypt_provider_token(bot_token)
    connection.scopes = scopes
    connection.updatedAt = utc_now()
    await connection.save()
    return connection


async def slack_connection(owner_id: str, team_id: str) -> IntegrationConnection | None:
    return await IntegrationConnection.find_one(
        IntegrationConnection.ownerId == owner_id,
        IntegrationConnection.provider == "slack",
        IntegrationConnection.externalId == team_id,
    )


async def slack_connections(owner_id: str) -> list[IntegrationConnection]:
    return await IntegrationConnection.find(
        IntegrationConnection.ownerId == owner_id,
        IntegrationConnection.provider == "slack",
    ).to_list()


async def save_github_connection(
    *,
    owner_id: str,
    account_name: str,
    personal_access_token: str,
) -> IntegrationConnection:
    """사용자의 GitHub PAT를 암호화해 저장한다.

    Gmail·Slack과 달리 OAuth를 거치지 않는다. 시연 범위에서는 OAuth App을 새로
    등록하는 비용이 크고, 어차피 저장·사용 방식(Fernet 암호화 후 서버에서만 복호화)은
    똑같기 때문이다. 사람마다 접근 권한이 다른 저장소를 봐야 하므로 서버 공용
    토큰 하나로는 안 되고, 사용자별로 묶여야 한다.
    """
    connection = await IntegrationConnection.find_one(
        IntegrationConnection.ownerId == owner_id,
        IntegrationConnection.provider == "github",
        IntegrationConnection.externalId == account_name,
    )
    encrypted = encrypt_provider_token(personal_access_token)
    if connection is None:
        connection = IntegrationConnection(
            ownerId=owner_id,
            provider="github",
            externalId=account_name,
            externalName=account_name,
            accessTokenEncrypted=encrypted,
        )
        await connection.insert()
        return connection

    connection.accessTokenEncrypted = encrypted
    connection.updatedAt = utc_now()
    await connection.save()
    return connection


async def github_connection(owner_id: str) -> IntegrationConnection | None:
    return (
        await IntegrationConnection.find(
            IntegrationConnection.ownerId == owner_id,
            IntegrationConnection.provider == "github",
        )
        .sort(-IntegrationConnection.updatedAt)
        .first_or_none()
    )


def access_token(connection: IntegrationConnection) -> str:
    return decrypt_provider_token(connection.accessTokenEncrypted)


def refresh_token(connection: IntegrationConnection) -> str | None:
    if connection.refreshTokenEncrypted is None:
        return None
    return decrypt_provider_token(connection.refreshTokenEncrypted)
