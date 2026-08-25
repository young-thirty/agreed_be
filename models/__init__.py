from models.contract import Contract
from models.requirement import Requirement

# init_beanie에 넘길 목록. 새 Document를 만들면 여기에 추가한다.
DOCUMENT_MODELS = [Contract, Requirement]

__all__ = ["Contract", "Requirement", "DOCUMENT_MODELS"]
