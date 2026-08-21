"""Pydantic request/response models."""

from pydantic import BaseModel, Field


# --- Analysis Request Models ---


class AnalysisRequest(BaseModel):
    """Base analysis request."""

    repo_url: str = ""
    branch: str = "main"
    pat_token: str = ""


class GithubAnalysisRequest(BaseModel):
    """GitHub-specific analysis request."""

    repo_url: str
    branch: str = "main"
    pat_token: str = ""


class UploadResponse(BaseModel):
    """Response after file upload."""

    analysis_id: str
    status: str = "processing"
    message: str = "Analysis started"


class AnalysisStatus(BaseModel):
    """Analysis progress status."""

    analysis_id: str
    status: str
    progress: int = 0
    current_step: str = ""
    message: str = ""


class AnalysisListItem(BaseModel):
    """Item in the analyses list."""

    analysis_id: str
    source_type: str = "upload"
    source_url: str | None = None
    created_at: str = ""
    status: str = "completed"


# --- Health ---


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"


# --- Error ---


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str


# --- Transformation Models ---


class TransformationDefinition(BaseModel):
    """Transformation definition for CRUD."""

    id: str = ""
    name: str = ""
    description: str = ""
    type: str = "custom"
    definition_path: str = ""
    published: bool = False


class TransformationDefinitionCreate(BaseModel):
    """Create a new transformation definition."""

    name: str
    description: str = ""
    definition_path: str = ""


class TransformationDefinitionUpdate(BaseModel):
    """Update an existing transformation definition."""

    name: str | None = None
    description: str | None = None
    definition_path: str | None = None
    published: bool | None = None


# --- Auth Models ---


class LoginRequest(BaseModel):
    """Login request payload."""

    username: str
    password: str


class TokenResponse(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class AuthConfig(BaseModel):
    """Auth configuration response."""

    mode: str = "disabled"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_domain: str = ""
    redirect_uri: str = ""


# --- Prompt Models ---


class PromptTemplate(BaseModel):
    """Prompt template model."""

    id: str = ""
    name: str = ""
    content: str = ""
    version: str = "1.0"
    agent: str = ""
    model: str = ""
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
