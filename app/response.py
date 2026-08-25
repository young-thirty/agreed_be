"""응답 규약.

모든 API는 아래 두 형태 중 하나만 돌려준다. 프론트엔드가 이 모양을 기대한다.

    { "ok": true,  "data": ... }
    { "ok": false, "error": "사용자가 그대로 읽을 한국어 문장" }

error에 스택 트레이스나 내부 식별자를 넣지 않는다. HTTP 상태 코드는
정상 200, 잘못된 입력 400, 서버 오류 500만 쓴다.
"""

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def ok(data: Any) -> JSONResponse:
    # by_alias=False가 중요하다. Beanie Document의 id 필드에는 alias='_id'가
    # 붙어 있어서, jsonable_encoder 기본값(by_alias=True)으로 내보내면 응답이
    # "_id"가 된다. 프론트엔드는 "id"를 기대하므로 그대로 두면 조용히 깨진다.
    return JSONResponse({"ok": True, "data": jsonable_encoder(data, by_alias=False)})


def fail(error: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error}, status_code=status)
