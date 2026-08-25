import os
from contextlib import asynccontextmanager

from beanie import init_beanie
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pymongo import AsyncMongoClient

from app.api import analyze, auth, contract, email, requirements, slack
from app.auth import SESSION_COOKIE_NAME
from app.response import fail, ok
from models import DOCUMENT_MODELS

load_dotenv()

MONGODB_URL = os.environ.get("MONGODB_URL", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "agreed")
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")
if FRONTEND_ORIGIN.strip() == "*":
    raise RuntimeError("쿠키 인증을 사용하므로 FRONTEND_ORIGIN에 *를 쓸 수 없습니다.")
FRONTEND_ORIGIN = FRONTEND_ORIGIN.rstrip("/")


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
        # Atlas URI에는 비밀번호가 포함될 수 있으므로 연결 문자열을 로그에 쓰지 않는다.
        raise RuntimeError(
            "MongoDB에 연결하지 못했습니다. 연결 정보와 서버 상태를 확인해 주세요."
        ) from error
    yield
    await client.close()


app = FastAPI(title="Agreed API", lifespan=lifespan)

# 프론트엔드(3000)와 API(8000)가 다른 출처라 CORS가 없으면 브라우저가 전부 막는다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, __: RequestValidationError):
    # FastAPI 기본 detail은 요청 input 전체를 포함할 수 있어 비밀번호를 반사한다.
    return fail("입력값 형식을 확인해 주세요.", 422)


@app.middleware("http")
async def reject_cross_origin_cookie_writes(request: Request, call_next):
    """SameSite=None 배포에서도 다른 사이트의 cookie-auth 변경 요청을 막는다."""

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.cookies.get(
        SESSION_COOKIE_NAME
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != FRONTEND_ORIGIN:
            return fail("허용되지 않은 출처의 요청입니다.", 403)
    return await call_next(request)

app.include_router(auth.router, prefix="/api")
app.include_router(analyze.router, prefix="/api")
app.include_router(contract.router, prefix="/api")
app.include_router(email.router, prefix="/api")
app.include_router(requirements.router, prefix="/api")
app.include_router(slack.router, prefix="/api")


@app.get("/api/health")
async def health():
    """배포가 살아 있는지 확인하는 용도다. 도메인 로직을 여기에 넣지 않는다."""
    return ok({"status": "up"})
