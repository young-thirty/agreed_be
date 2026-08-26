"""DeepSeek 호출.

DeepSeek은 OpenAI 호환 API라 openai 패키지에 base_url만 바꿔 쓴다.
키는 서버 환경변수에서만 읽는다. 프론트엔드로 내려보내지 않는다.

구조화 출력은 function calling 대신 JSON mode를 쓴다. 어차피 받은 결과를
Pydantic으로 한 번 더 검증하므로(L1), 실패 지점이 적은 쪽을 택했다.
"""

import os

from openai import AsyncOpenAI

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
EXTRACT_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

# 타임아웃 8초. 재시도는 이 클라이언트가 아니라 extract.py가 L1 검증 실패 시
# 직접 1회만 한다. max_retries를 0으로 둬야 타임아웃까지 중복 재시도되지 않는다.
REQUEST_TIMEOUT_SECONDS = 8.0

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "DEEPSEEK_API_KEY가 설정되지 않았습니다. .env 파일을 확인해 주세요."
            )
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_BASE_URL,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
    return _client


def has_api_key() -> bool:
    """키가 없어도 폴백 경로로 시연할 수 있으므로, 호출 전에 확인만 한다."""
    return bool(os.environ.get("DEEPSEEK_API_KEY"))
