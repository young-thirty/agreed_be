"""프로젝트 자료 분류와 요약을 담당하는 LLM 경계."""

from infra.llm.harness import run_json
from infra.llm.prompts import PROJECT_MATERIAL_SYSTEM_PROMPT
from infra.llm.schemas import MaterialClassificationResult


async def classify_project_material(
    *, file_name: str, extracted_text: str
) -> MaterialClassificationResult | None:
    """추출 텍스트가 있는 자료만 분류·요약한다. 실패는 None으로 강등한다."""

    if not extracted_text.strip():
        return None
    return await run_json(
        system_prompt=PROJECT_MATERIAL_SYSTEM_PROMPT,
        user_content=f"파일명: {file_name}\n텍스트: {extracted_text[:8000]}",
        schema=MaterialClassificationResult,
    )
