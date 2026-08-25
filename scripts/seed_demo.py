"""현재 로그인 사용자에게 10분 시연용 프로젝트 데이터를 넣는다.

사용법: ``python scripts/seed_demo.py --email you@example.com``
기존 데이터는 삭제하지 않고 같은 이름의 프로젝트가 있으면 재사용한다.
"""

import argparse
import asyncio
import os
from datetime import datetime

from beanie import init_beanie
from dotenv import load_dotenv
from pymongo import AsyncMongoClient

from models import ClientRequest, Project, ProjectMaterial, SourceMessage, User


async def seed(email: str) -> None:
    load_dotenv()
    client = AsyncMongoClient(os.environ.get("MONGODB_URL", "mongodb://localhost:27017"))
    try:
        await init_beanie(
            database=client[os.environ.get("MONGODB_DB", "agreed")],
            document_models=[
                User, Project, SourceMessage, ClientRequest, ProjectMaterial,
            ],
        )
        user = await User.find_one(User.email == email.lower())
        if user is None:
            raise SystemExit("먼저 /api/auth/signup으로 회원가입한 이메일을 사용하세요.")
        project = await Project.find_one(Project.ownerId == user.id, Project.name == "시연 프로젝트")
        if project is None:
            project = Project(ownerId=user.id, name="시연 프로젝트", clientName="Agreed 데모 클라이언트",
                              contractPrice=5000000, status="ACTIVE", statusRank=0)
            await project.insert()
        print(f"projectId={project.id}")
        print("프로젝트 seed 완료: /api/projects에서 확인하세요.")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    asyncio.run(seed(args.email))
