from models.contract import Contract
from models.integration import IntegrationConnection
from models.requirement import Requirement
from models.session import Session
from models.user import User

# init_beanie에 넘길 목록. 새 Document를 만들면 여기에 추가한다.
DOCUMENT_MODELS = [
    User,
    Session,
    Contract,
    Requirement,
    IntegrationConnection,
]

__all__ = [
    "Contract",
    "IntegrationConnection",
    "Requirement",
    "Session",
    "User",
    "DOCUMENT_MODELS",
]
