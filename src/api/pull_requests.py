"""
Pull Request API endpoints.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional

from src.services.github_pr import get_github_pr_service
from src.services.gcs_storage import get_gcs_storage
from src.services.supabase import get_supabase_service
from src.services.validation import get_validator
from src.utils.clerk_auth import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pull-requests", tags=["pull-requests"])


class CreatePRRequest(BaseModel):
    """Request to create a PR with generated infrastructure."""

    project_id: str
    generation_id: str
    base_branch: str = "main"


class CreatePRResponse(BaseModel):
    """Response from PR creation."""

    pr_number: int
    pr_url: str
    branch: str
    validation_warnings: list[str] = []


class PRStatusResponse(BaseModel):
    """PR status information."""

    pr_number: int
    pr_url: str
    state: str  # open, closed, merged
    merged: bool
    mergeable: Optional[bool]
    created_at: str
    updated_at: str


@router.post("/create", response_model=CreatePRResponse)
async def create_pull_request(pr_request: CreatePRRequest, request: Request):
    """
    Create a GitHub PR with generated infrastructure files.

    Steps:
    1. Validate generated files
    2. Fetch files from S3
    3. Create branch and commit files
    4. Create PR
    5. Update database with PR info
    """
    try:
        user_id = await get_current_user_id(request)
        supabase = get_supabase_service()
        gcs_storage = get_gcs_storage()
        pr_service = get_github_pr_service()
        validator = get_validator()

        # 1. Get project and generation details
        project = supabase.get_project_by_id(pr_request.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if project["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        generation = supabase.get_generation_by_id(pr_request.generation_id)
        if not generation:
            raise HTTPException(status_code=404, detail="Generation not found")

        if generation["status"] != "completed":
            raise HTTPException(
                status_code=400, detail="Generation not completed - cannot create PR"
            )

        # 2. Fetch files from GCS
        owner, repo = project["repository_name"].split("/")
        files_data = await gcs_storage.get_repository_files(owner, repo)

        if not files_data:
            raise HTTPException(status_code=404, detail="Generated files not found in GCS")

        logger.info(f"Fetched {len(files_data)} files from GCS")

        # 3. Validate files before creating PR
        dockerfile_content = None
        terraform_files = {}
        all_warnings = []

        for file in files_data:
            filename = file.get("path") or file.get("filename", "")
            if filename == "Dockerfile":
                dockerfile_content = file["content"]
                logger.info(
                    f"Dockerfile content length: {len(dockerfile_content) if dockerfile_content else 0}"
                )
                if dockerfile_content:
                    logger.info(f"Dockerfile first 100 chars: {dockerfile_content[:100]}")
            elif filename.endswith(".tf"):
                terraform_files[filename] = file["content"]

        if dockerfile_content:
            framework = generation.get("project_context", {}).get("framework")
            dockerfile_result = validator.validate_dockerfile(dockerfile_content, framework)
            all_warnings.extend(dockerfile_result.warnings)

            if not dockerfile_result.is_valid:
                logger.error(f"Dockerfile validation failed: {dockerfile_result.errors}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Dockerfile validation failed: {', '.join(dockerfile_result.errors)}",
                )

        if terraform_files:
            terraform_result = validator.validate_terraform(terraform_files)
            all_warnings.extend(terraform_result.warnings)

            if not terraform_result.is_valid:
                logger.error(f"Terraform validation failed: {terraform_result.errors}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Terraform validation failed: {', '.join(terraform_result.errors)}",
                )

        logger.info(
            f"Validation passed with {len(all_warnings)} warnings for project {pr_request.project_id}"
        )

        # 4. Create PR
        pr_result = await pr_service.create_infrastructure_pr(
            installation_id=project["installation_id"],
            owner=owner,
            repo=repo,
            session_id=generation["session_id"],
            files=files_data,
            context=generation.get("project_context", {}),
            base_branch=pr_request.base_branch,
        )

        # 5. Update database with PR info
        supabase.update_generation_pr_info(
            generation_id=pr_request.generation_id,
            pr_number=pr_result["pr_number"],
            pr_url=pr_result["pr_url"],
            pr_branch=pr_result["branch"],
        )

        supabase.update_project_generation_status(
            project_id=pr_request.project_id, status="pr_created", increment_count=False
        )

        logger.info(f"Created PR #{pr_result['pr_number']} for project {pr_request.project_id}")

        return CreatePRResponse(
            pr_number=pr_result["pr_number"],
            pr_url=pr_result["pr_url"],
            branch=pr_result["branch"],
            validation_warnings=all_warnings,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create PR: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create PR: {str(e)}")


@router.get("/{project_id}/status", response_model=PRStatusResponse)
async def get_pr_status(project_id: str, request: Request):
    """
    Get the status of the PR for a project.
    """
    try:
        user_id = await get_current_user_id(request)
        supabase = get_supabase_service()
        pr_service = get_github_pr_service()

        # Get project
        project = supabase.get_project_by_id(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if project["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        # Get latest generation with PR info
        generation = supabase.get_latest_generation_by_project(project_id)
        if not generation or not generation.get("pr_number"):
            raise HTTPException(status_code=404, detail="No PR found for this project")

        # Fetch PR status from GitHub
        owner, repo = project["repository_name"].split("/")
        pr_data = await pr_service.github.get_pull_request(
            installation_id=project["installation_id"],
            owner=owner,
            repo=repo,
            pr_number=generation["pr_number"],
        )

        return PRStatusResponse(
            pr_number=pr_data["number"],
            pr_url=pr_data["html_url"],
            state=pr_data["state"],
            merged=pr_data.get("merged", False),
            mergeable=pr_data.get("mergeable"),
            created_at=pr_data["created_at"],
            updated_at=pr_data["updated_at"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get PR status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get PR status: {str(e)}")
