"""
Pydantic models for API requests and responses.
"""

from pydantic import BaseModel, HttpUrl, Field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class TemplateType(str, Enum):
    """Available infrastructure templates."""

    DOCKER = "docker"
    DOCKER_COMPOSE = "docker-compose"
    KUBERNETES = "kubernetes"
    ECS_FARGATE = "ecs-fargate"
    EC2 = "ec2"
    LAMBDA = "lambda"
    CLOUD_RUN = "cloud-run"


class WorkflowStatus(str, Enum):
    """Workflow execution status."""

    PENDING = "pending"
    STARTED = "started"
    ANALYZING = "analyzing"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class DeploymentStatus(str, Enum):
    """Deployment execution status."""

    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowStartRequest(BaseModel):
    """Request to start infrastructure generation workflow."""

    repository_url: HttpUrl = Field(
        ..., description="GitHub repository URL", examples=["https://github.com/user/flask-app"]
    )
    installation_id: int = Field(..., description="GitHub App installation ID")
    template_type: TemplateType = Field(
        default=TemplateType.DOCKER, description="Infrastructure template to generate"
    )
    cloud_provider: str = Field(default="gcp", description="Cloud provider: 'gcp' or 'aws'")
    branch: str = Field(default="main", description="Git branch to analyze")
    project_id: Optional[str] = Field(
        default=None, description="Project UUID to link generation to"
    )


class ProjectContext(BaseModel):
    """Analyzed project context."""

    language: str
    framework: Optional[str] = None
    dependencies: List[str] = []
    build_command: Optional[str] = None
    start_command: Optional[str] = None
    port: Optional[int] = None
    env_vars: Dict[str, str] = {}
    file_structure: Dict[str, Any] = {}


class GeneratedFile(BaseModel):
    """Generated infrastructure file."""

    path: str
    content: str
    description: Optional[str] = None


class WorkflowStartResponse(BaseModel):
    """Response after starting workflow."""

    session_id: str = Field(..., description="Unique session identifier")
    status: WorkflowStatus
    message: str
    stream_url: str = Field(..., description="SSE endpoint for real-time updates")


class WorkflowStatusResponse(BaseModel):
    """Current workflow status."""

    session_id: str
    status: WorkflowStatus
    current_step: Optional[str] = None
    progress: int = Field(ge=0, le=100, description="Progress percentage")
    files: List[GeneratedFile] = []
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class WorkflowCompleteResponse(BaseModel):
    """Workflow completion response."""

    session_id: str
    status: WorkflowStatus
    files: List[GeneratedFile]
    download_urls: Dict[str, str] = {}
    project_context: Optional[ProjectContext] = None
    execution_time_seconds: float


class SSEEvent(BaseModel):
    """Server-Sent Event."""

    event: str = "message"
    data: Dict[str, Any]
    id: Optional[str] = None
    retry: Optional[int] = None


class LogEvent(BaseModel):
    """Agent log event for streaming."""

    timestamp: datetime
    agent: str
    level: str
    message: str
    metadata: Dict[str, Any] = {}


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "healthy"
    version: str = "0.1.0"
    environment: str
    services: Dict[str, str] = {}


# Deployment API Models


class DeploymentStartRequest(BaseModel):
    """Request to start cross-account deployment."""

    role_arn: str = Field(..., description="AWS IAM role ARN for cross-account access")
    external_id: str = Field(..., description="External ID for role assumption security")
    files: List[Dict[str, Any]] = Field(..., description="Generated infrastructure files")
    project_id: Optional[str] = Field(default=None, description="Project UUID")


class DeploymentStartResponse(BaseModel):
    """Response from deployment start request."""

    session_id: str
    status: str
    message: str
    stream_url: str


class DeploymentStatusResponse(BaseModel):
    """Current deployment status."""

    session_id: str
    status: str
    message: str
    logs: List[Dict[str, Any]] = []
    result: Optional[Dict[str, Any]] = None
