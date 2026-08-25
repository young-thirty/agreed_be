import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import AsyncMongoClient

from app.api import analyze, contract, requirements
from app.response import ok
from models import DOCUMENT_MODELS

load_dotenv()

MONGODB_URL = os.environ.get("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "agreed")
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """init_beanie가 반드시 먼저 돌아야 한다.

    Beanie Document는 init_beanie 전에는 인스턴스 생성조차 되지 않고
    CollectionWasNotInitialized를 던진다. 모듈을 불러오는 시점에 Document를
    만드는 코드(예: 전역 목 데이터)를 두면 앱이 아예 뜨지 않는다.
    """
    # 서버 선택 타임아웃을 짧게 둔다. 기본값(30초)이면 MongoDB가 안 떠 있을 때
    # 기동 중에 아무 메시지 없이 멈춰 있는 것처럼 보인다.
    client = AsyncMongoClient(MONGODB_URL, serverSelectionTimeoutMS=3000)
    try:
        await init_beanie(database=client[MONGODB_DB], document_models=DOCUMENT_MODELS)
    except Exception as error:
        raise RuntimeError(
            f"MongoDB({MONGODB_URL})에 연결하지 못했습니다. "
            "docker run -d -p 27017:27017 --name agreed-mongo mongo 로 먼저 띄워 주세요."
        ) from error
    yield
    await client.close()


app = FastAPI(title="Agreed API", lifespan=lifespan)

# 프론트엔드(3000)와 API(8000)가 다른 출처라 CORS가 없으면 브라우저가 전부 막는다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze.router, prefix="/api")
app.include_router(contract.router, prefix="/api")
app.include_router(requirements.router, prefix="/api")


@app.get("/api/health")
async def health():
    """배포가 살아 있는지 확인하는 용도다. 도메인 로직을 여기에 넣지 않는다."""
    return ok({"status": "up"})
