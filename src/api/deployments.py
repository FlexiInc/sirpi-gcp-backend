import logging
import uuid
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, HttpUrl

from src.agentcore.orchestrator import SirpiOrchestrator, CloudProvider
# Note: We will need a service to handle git operations.
# from backend.src.services.git_service import GitService 

# Setup logger
logger = logging.getLogger(__name__)

# Create a new router for deployment-related endpoints
router = APIRouter(
    prefix="/api/v1/deployments",
    tags=["Deployments"]
)

class CreateDeploymentRequest(BaseModel):
    """Defines the request body for creating a new deployment."""
    github_repo_url: HttpUrl
    project_id: str
    cloud_provider: CloudProvider = "gcp"  # User chooses GCP or AWS

class CreateDeploymentResponse(BaseModel):
    """Defines the response body for a successful deployment initiation."""
    message: str
    deployment_id: str
    cloud_provider: str
    status_endpoint: str

# This is a placeholder for a real Git cloning service.
# In a production app, this would be a robust service that handles
# authentication, cloning, and cleanup.
class GitServicePlaceholder:
    def clone_repo(self, repo_url: str, target_dir: str):
        logger.info(f"Placeholder: Simulating cloning {repo_url} to {target_dir}")
        # In a real implementation, you would use a library like GitPython
        # or run the `git` command as a subprocess.
        # For now, we'll just create the directory.
        import os
        os.makedirs(target_dir, exist_ok=True)
        logger.info(f"Placeholder: Created directory {target_dir}")


async def agent_workflow_background_task(
    repo_url: str,
    local_path: str, 
    deployment_id: str,
    cloud_provider: CloudProvider,
    user_id: str
):
    """
    This function is designed to be run in the background. It initializes
    the orchestrator and executes the full agent pipeline.
    """
    import asyncio
    logger.info(f"Starting background agent workflow for deployment_id: {deployment_id}")
    
    try:
        orchestrator = SirpiOrchestrator(cloud_provider=cloud_provider)
        final_state = await orchestrator.run_workflow(
            github_repo_url=repo_url,
            local_repo_path=local_path,
            user_id=user_id,
            session_id=deployment_id
        )
        
        if final_state.status == "SUCCESS":
            logger.info(f"Agent workflow for deployment_id: {deployment_id} completed successfully.")
            # Here you would update your database with the final status and artifacts
            # db_service.update_deployment_success(deployment_id, final_state.to_dict())
        else:
            logger.error(f"Agent workflow for deployment_id: {deployment_id} failed. Error: {final_state.error}")
            # Here you would update your database with the failure status
            # db_service.update_deployment_failure(deployment_id, final_state.error)

    except Exception as e:
        logger.critical(f"A critical error occurred in the background task for deployment {deployment_id}: {e}", exc_info=True)
        # db_service.update_deployment_failure(deployment_id, "A critical orchestrator error occurred.")
    finally:
        # Here you would add cleanup logic, like removing the cloned repository
        # shutil.rmtree(local_path)
        logger.info(f"Background task finished for deployment_id: {deployment_id}")


@router.post("", status_code=202)
async def start_new_deployment(
    request: CreateDeploymentRequest,
    background_tasks: BackgroundTasks
) -> CreateDeploymentResponse:
    """
    Initiates a new deployment process for a given GitHub repository.

    This endpoint starts the long-running agent workflow as a background task
    and immediately returns a response to the client.
    """
    deployment_id = str(uuid.uuid4())
    logger.info(f"Received request to start new deployment {deployment_id} for repo: {request.github_repo_url}")

    # Define the local directory where the repository will be cloned
    # Using a unique directory per deployment is crucial
    local_repo_path = f"/tmp/sirpi_clones/{deployment_id}"

    # In a real application, you would first create a record in your database
    # for this new deployment with a "PENDING" status.
    # db_service.create_deployment_record(deployment_id, request.project_id, request.github_repo_url)

    # --- Git Cloning ---
    # This is a critical step that requires a dedicated, secure service.
    # We are using a placeholder here for demonstration.
    try:
        git_service = GitServicePlaceholder()
        git_service.clone_repo(str(request.github_repo_url), local_repo_path)
    except Exception as e:
        logger.error(f"Failed to clone repository {request.github_repo_url}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to clone the specified repository.")

    # Add the long-running agent workflow to be executed in the background
    background_tasks.add_task(
        agent_workflow_background_task,
        repo_url=str(request.github_repo_url),
        local_path=local_repo_path,
        deployment_id=deployment_id,
        cloud_provider=request.cloud_provider,
        user_id=f"project_{request.project_id}"
    )

    logger.info(f"Deployment {deployment_id} has been successfully scheduled to run in the background.")

    return CreateDeploymentResponse(
        message=f"Deployment to {request.cloud_provider.upper()} initiated successfully",
        deployment_id=deployment_id,
        cloud_provider=request.cloud_provider,
        status_endpoint=f"/api/v1/deployments/{deployment_id}/status"
    )