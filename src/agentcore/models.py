"""
Repository context models for agent analysis.
"""

from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from enum import Enum


class DeploymentTarget(str, Enum):
    """Supported deployment targets."""

    FARGATE = "fargate"
    EC2 = "ec2"
    LAMBDA = "lambda"


class RepositoryContext(BaseModel):
    """
    Structured repository analysis context.
    Output from Context Analyzer Agent.
    """

    language: str = Field(..., description="Primary programming language")
    framework: Optional[str] = Field(None, description="Framework name (if detected)")
    runtime: str = Field(..., description="Runtime version required (e.g., python3.12, node20)")
    package_manager: str = Field(..., description="Package manager (npm, pip, uv, etc.)")
    dependencies: Dict[str, str] = Field(
        default_factory=dict, description="Package dependencies with versions"
    )
    deployment_target: DeploymentTarget = Field(..., description="Recommended deployment target")
    ports: List[int] = Field(default_factory=list, description="Exposed application ports")
    environment_vars: List[str] = Field(
        default_factory=list, description="Required environment variables"
    )
    health_check_path: Optional[str] = Field(None, description="Health check endpoint path")
    start_command: Optional[str] = Field(None, description="Command to start application")
    build_command: Optional[str] = Field(None, description="Command to build application")

    # Existing infrastructure detection
    has_existing_dockerfile: bool = Field(
        default=False, description="Whether repository has existing Dockerfile"
    )
    existing_dockerfile_content: Optional[str] = Field(
        None, description="Content of existing Dockerfile if found"
    )
    has_existing_terraform: bool = Field(
        default=False, description="Whether repository has existing Terraform files"
    )
    existing_terraform_files: Dict[str, str] = Field(
        default_factory=dict, description="Existing Terraform file contents (filename -> content)"
    )
    terraform_location: Optional[str] = Field(
        None, description="Location of Terraform files (terraform/, root, etc.)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "language": "python",
                "framework": "fastapi",
                "runtime": "python3.12",
                "package_manager": "uv",
                "dependencies": {"fastapi": "0.115.0", "uvicorn": "0.30.0"},
                "deployment_target": "fargate",
                "ports": [8000],
                "environment_vars": ["DATABASE_URL", "API_KEY"],
                "health_check_path": "/health",
                "start_command": "uvicorn main:app --host 0.0.0.0 --port 8000",
                "build_command": None,
            }
        }


class RawRepositoryData(BaseModel):
    """
    Raw data fetched from GitHub before AI analysis.
    """

    owner: str
    repo: str
    files: List[Dict] = Field(default_factory=list, description="File tree structure")
    package_files: Dict[str, str] = Field(
        default_factory=dict, description="Package manager files content"
    )
    config_files: Dict[str, str] = Field(
        default_factory=dict, description="Configuration files content"
    )
    detected_language: Optional[str] = None
    is_monorepo: bool = Field(default=False, description="Whether this is a monorepo with backend/frontend")
    monorepo_subdirectory: Optional[str] = Field(None, description="Subdirectory being analyzed (e.g., 'backend')")

    # Existing infrastructure files
    existing_dockerfile: Optional[str] = Field(
        None, description="Existing Dockerfile content if found"
    )
    existing_terraform: Dict[str, str] = Field(
        default_factory=dict, description="Existing Terraform files (path -> content)"
    )
    terraform_location: Optional[str] = Field(None, description="Where Terraform files are located")
