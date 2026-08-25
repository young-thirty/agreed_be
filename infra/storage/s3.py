"""원본 파일을 S3에 두는 얇은 래퍼.

ProjectMaterial.storageKey가 가리키는 곳이 여기다. 추출 텍스트만 Mongo에
남기고 원본 바이트는 S3에 둔다. 문서 자체를 컨텍스트에 그대로 밀어 넣지
않기 위해서다.

boto3를 새로 추가한다. AWS 공식 SDK이고 배포가 이미 App Runner(AWS)라 인스턴스
role만 있으면 별도 키 없이 동작한다. 버킷이 설정되지 않은 로컬 개발 환경에서는
has_s3()가 False를 돌려주고 호출부가 저장을 건너뛴다 — 파일 저장 실패가
채널 동기화 전체를 막지 않는다.
"""

import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

_BUCKET = os.environ.get("S3_BUCKET_NAME", "")
_client = None


def has_s3() -> bool:
    return bool(_BUCKET)


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"))
    return _client


def put_object(key: str, data: bytes, content_type: str | None = None) -> str | None:
    """업로드에 성공하면 key를, 실패하거나 버킷이 없으면 None을 돌려준다."""
    if not has_s3():
        return None
    try:
        kwargs: dict = {"Bucket": _BUCKET, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        _get_client().put_object(**kwargs)
        return key
    except (BotoCoreError, ClientError):
        return None


def get_object(key: str) -> bytes | None:
    if not has_s3():
        return None
    try:
        response = _get_client().get_object(Bucket=_BUCKET, Key=key)
        return response["Body"].read()
    except (BotoCoreError, ClientError):
        return None
