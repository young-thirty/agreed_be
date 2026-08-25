from models.contract import Contract
from models.analysis_run import AnalysisRun
from models.client_request import ClientRequest
from models.integration import IntegrationConnection
from models.project import Project
from models.project_material import ProjectMaterial
from models.requirement import Requirement
from models.session import Session
from models.source_link import ProjectSourceLink
from models.source_message import SourceMessage
from models.user import User

# init_beanie에 넘길 목록. 새 Document를 만들면 여기에 추가한다.
DOCUMENT_MODELS = [
    User,
    Session,
    Contract,
    Project,
    ProjectSourceLink,
    SourceMessage,
    ClientRequest,
    ProjectMaterial,
    AnalysisRun,
    Requirement,
    IntegrationConnection,
]

__all__ = [
    "Contract",
    "Project",
    "ProjectSourceLink",
    "SourceMessage",
    "ClientRequest",
    "ProjectMaterial",
    "AnalysisRun",
    "IntegrationConnection",
    "Requirement",
    "Session",
    "User",
    "DOCUMENT_MODELS",
]
