"""
AWS Deployment API Endpoints.
Handles build, plan, apply, and destroy operations for AWS ECS Fargate.
"""

import logging
import json
from fastapi import APIRouter, HTTPException, Depends, Request

from src.services.deployment.sandbox_manager import SandboxManager
from src.services.deployment.aws_deployment import AWSDeploymentService
from src.services.deployment.aws_docker_build import AWSDockerBuildService
from src.services.supabase import supabase
from src.services.gcs_storage import get_gcs_storage
from src.utils.clerk_auth import get_current_user_id
from src.core.config import settings
from src.api.deployment_logs import register_deployment, send_log, send_completion
from src.api.env_vars import get_decrypted_env_vars
import asyncio

router = APIRouter(prefix="/deployment", tags=["AWS Deployment"])
logger = logging.getLogger(__name__)


@router.get("/projects/{project_id}/logs")
async def get_aws_deployment_logs(project_id: str, user_id: str = Depends(get_current_user_id)):
    """
    Get all deployment logs for an AWS project (build, plan, apply, destroy).
    """
    try:
        # Verify project ownership
        project = supabase.get_project_by_id(project_id)
        if not project or project["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="Project not found")

        # Get logs for all operation types
        logs = []
        for operation_type in ["build_image", "plan", "apply", "destroy"]:
            operation_logs = supabase.get_deployment_logs(project_id, operation_type)
            if operation_logs and operation_logs.get("logs"):
                logs.append(
                    {
                        "operation_type": operation_type,
                        "status": operation_logs.get("status", "unknown"),
                        "logs": operation_logs.get("logs", []),
                        "duration_seconds": operation_logs.get("duration_seconds"),
                        "created_at": operation_logs.get("created_at"),
                    }
                )

        return {"success": True, "data": {"logs": logs}}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get deployment logs: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve logs")


async def get_aws_connection_for_project(project_id: str, user_id: str) -> dict:
    """
    Get AWS connection details for a project.

    Returns:
        Dict with role_arn and external_id
    """
    project = supabase.get_project_by_id(project_id)
    if not project or project["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Project not found")

    aws_connection_id = project.get("aws_connection_id")
    if not aws_connection_id:
        raise HTTPException(
            status_code=400,
            detail="No AWS connection configured. Please connect your AWS account first.",
        )

    aws_connection = supabase.get_aws_connection_by_id(aws_connection_id)
    if not aws_connection or aws_connection.get("status") != "verified":
        raise HTTPException(
            status_code=400,
            detail="AWS connection not verified. Please verify your AWS connection.",
        )

    role_arn = aws_connection.get("role_arn")
    external_id = aws_connection.get("external_id")

    if not role_arn or not external_id:
        raise HTTPException(
            status_code=400, detail="AWS connection incomplete. Missing role ARN or external ID."
        )

    return {"role_arn": role_arn, "external_id": external_id, "project": project}


@router.post("/projects/{project_id}/build_image")
async def build_aws_image(
    project_id: str, request: Request, user_id: str = Depends(get_current_user_id)
):
    """
    Build Docker image and push to AWS ECR.
    """
    import time

    start_time = time.time()

    try:
        # Get AWS connection
        aws_data = await get_aws_connection_for_project(project_id, user_id)
        project = aws_data["project"]
        role_arn = aws_data["role_arn"]
        external_id = aws_data["external_id"]

        # Get latest generation
        generation = supabase.get_latest_generation_by_project(project_id)
        if not generation:
            raise HTTPException(status_code=404, detail="No generation found for project")

        # Get generated Dockerfile from GCS
        gcs = get_gcs_storage()
        owner, repo = project["repository_name"].split("/")
        files = await gcs.get_repository_files(owner, repo)

        dockerfile = next((f for f in files if f.get("path") == "Dockerfile"), None)
        if not dockerfile:
            raise HTTPException(status_code=404, detail="Dockerfile not found in generated files")

        # Register deployment for SSE streaming
        register_deployment(project_id)
        await send_log(project_id, "Starting Docker image build...")

        # Create sandbox
        sandbox = SandboxManager(template_id=settings.e2b_template_id)
        aws_docker_service = AWSDockerBuildService(sandbox, role_arn, external_id)

        # Collect logs
        collected_logs = []

        def collecting_callback(message: str):
            """Callback that both sends to SSE and collects for database."""
            collected_logs.append(message)
            asyncio.create_task(send_log(project_id, message))

        async with sandbox:
            # Set log callback FIRST
            sandbox.set_log_callback(collecting_callback)

            # Build and push image
            image_uri = await aws_docker_service.build_and_push_image(
                repository_url=project["repository_url"],
                dockerfile_content=dockerfile["content"],
                image_name=project["name"],
            )

            sandbox._log(f"✅ Image pushed successfully: {image_uri}")

        # Store logs in database
        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="build_image",
            logs=collected_logs,
            status="success",
            duration_seconds=int(duration),
        )

        # Update project status
        supabase.update_project_deployment_status(project_id, "image_built")

        # Send completion signal
        await send_completion(project_id)

        return {"success": True, "image_uri": image_uri, "duration_seconds": int(duration)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Docker build failed: {e}", exc_info=True)

        # Store error logs
        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="build_image",
            logs=[f"Error: {str(e)}"],
            status="failed",
            duration_seconds=int(duration),
        )

        await send_completion(project_id)
        raise HTTPException(status_code=500, detail=f"Build failed: {str(e)}")


@router.post("/projects/{project_id}/plan")
async def plan_aws_deployment(
    project_id: str, request: Request, user_id: str = Depends(get_current_user_id)
):
    """
    Generate Terraform plan for AWS ECS Fargate deployment.
    """
    import time

    start_time = time.time()

    try:
        # Get AWS connection
        aws_data = await get_aws_connection_for_project(project_id, user_id)
        project = aws_data["project"]
        role_arn = aws_data["role_arn"]
        external_id = aws_data["external_id"]

        # Get Terraform files from GCS
        gcs = get_gcs_storage()
        owner, repo = project["repository_name"].split("/")
        files = await gcs.get_repository_files(owner, repo)

        tf_files = {f["path"]: f["content"] for f in files if f["path"].endswith(".tf")}

        if not tf_files:
            raise HTTPException(status_code=404, detail="No Terraform files found")

        # Get image URI from build logs
        build_logs = supabase.get_deployment_logs(project_id, "build_image")
        image_uri = None
        for log in build_logs.get("logs", []):
            if ".ecr." in log and ".amazonaws.com" in log:
                import re

                match = re.search(
                    r"([0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+)", log
                )
                if match:
                    image_uri = match.group(1)
                    break

        if not image_uri:
            raise HTTPException(
                status_code=400, detail="No Docker image found. Please build the image first."
            )

        # Get environment variables
        env_vars = await get_decrypted_env_vars(project_id)

        # Register for SSE streaming
        register_deployment(project_id)
        await send_log(project_id, "Starting Terraform planning...")

        # Create sandbox
        sandbox = SandboxManager(template_id=settings.e2b_template_id)
        aws_service = AWSDeploymentService(sandbox, role_arn, external_id)

        # Collect logs
        collected_logs = []

        def collecting_callback(message: str):
            """Callback that both sends to SSE and collects for database."""
            collected_logs.append(message)
            asyncio.create_task(send_log(project_id, message))

        async with sandbox:
            # Set log callback FIRST
            sandbox.set_log_callback(collecting_callback)

            # Configure AWS credentials
            await aws_service._configure_aws_credentials()

            # Write Terraform files
            tf_dir = "/home/user/terraform"
            await sandbox.run_command(f"mkdir -p {tf_dir}", stream_output=False)

            # Check if any TF file has a backend block and remove it
            for filename, content in tf_files.items():
                if 'backend "s3"' in content or 'backend "gcs"' in content:
                    sandbox._log(f"⚠️ Found backend configuration in {filename}, removing it...")
                    import re

                    content = re.sub(r'backend\s+"[^"]+"\s*\{[^}]*\}', "", content, flags=re.DOTALL)

                await sandbox.write_file(f"{tf_dir}/{filename}", content)
                sandbox._log(f"Wrote {filename}")

            # Setup S3 state backend
            await aws_service.setup_terraform_state(project["name"], tf_dir)

            # Create tfvars with image URI, app name, ECR repository name, and env vars
            app_name = project["name"].lower()
            ecr_repository_name = f"sirpi/{app_name}"

            tfvars_content = f'''image_uri = "{image_uri}"
app_name = "{app_name}"
ecr_repository_name = "{ecr_repository_name}"
'''

            if env_vars:
                # Convert env vars to Terraform map format
                env_vars_tf = "{\n"
                for key, value in env_vars.items():
                    # Escape quotes in values
                    escaped_value = value.replace('"', '\\"')
                    env_vars_tf += f'  "{key}" = "{escaped_value}"\n'
                env_vars_tf += "}"
                tfvars_content += f"app_env_vars = {env_vars_tf}\n"

            await sandbox.write_file(f"{tf_dir}/terraform.tfvars", tfvars_content)
            sandbox._log("Wrote terraform.tfvars")

            # Initialize Terraform
            await aws_service.terraform_init(tf_dir)

            # Run plan
            plan_output = await aws_service.terraform_plan(tf_dir, "terraform.tfvars")

        # Store logs
        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="plan",
            logs=collected_logs,
            status="success",
            duration_seconds=int(duration),
        )

        # Update project status
        supabase.update_project_deployment_status(project_id, "plan_generated")

        # Send completion signal
        await send_completion(project_id)

        return {"success": True, "plan_output": plan_output, "duration_seconds": int(duration)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Terraform plan failed: {e}", exc_info=True)

        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="plan",
            logs=[f"Error: {str(e)}"],
            status="failed",
            duration_seconds=int(duration),
        )

        await send_completion(project_id)
        raise HTTPException(status_code=500, detail=f"Plan failed: {str(e)}")


@router.post("/projects/{project_id}/apply")
async def apply_aws_deployment(
    project_id: str, request: Request, user_id: str = Depends(get_current_user_id)
):
    """
    Deploy infrastructure to AWS ECS Fargate.
    """
    import time

    start_time = time.time()

    try:
        # Get AWS connection
        aws_data = await get_aws_connection_for_project(project_id, user_id)
        project = aws_data["project"]
        role_arn = aws_data["role_arn"]
        external_id = aws_data["external_id"]

        # Get Terraform files from GCS
        gcs = get_gcs_storage()
        owner, repo = project["repository_name"].split("/")
        files = await gcs.get_repository_files(owner, repo)

        tf_files = {f["path"]: f["content"] for f in files if f["path"].endswith(".tf")}

        # Get image URI from build logs
        build_logs = supabase.get_deployment_logs(project_id, "build_image")
        image_uri = None
        for log in build_logs.get("logs", []):
            if ".ecr." in log and ".amazonaws.com" in log:
                import re

                match = re.search(
                    r"([0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+)", log
                )
                if match:
                    image_uri = match.group(1)
                    break

        # Get environment variables
        env_vars = await get_decrypted_env_vars(project_id)

        # Register for SSE streaming
        register_deployment(project_id)
        await send_log(project_id, "Starting deployment...")

        # Create sandbox
        sandbox = SandboxManager(template_id=settings.e2b_template_id)
        aws_service = AWSDeploymentService(sandbox, role_arn, external_id)

        # Collect logs
        collected_logs = []

        def collecting_callback(message: str):
            collected_logs.append(message)
            asyncio.create_task(send_log(project_id, message))

        async with sandbox:
            sandbox.set_log_callback(collecting_callback)

            # Configure AWS credentials
            await aws_service._configure_aws_credentials()

            # Write Terraform files (same as plan)
            tf_dir = "/home/user/terraform"
            await sandbox.run_command(f"mkdir -p {tf_dir}", stream_output=False)

            for filename, content in tf_files.items():
                if 'backend "s3"' in content or 'backend "gcs"' in content:
                    import re

                    content = re.sub(r'backend\s+"[^"]+"\s*\{[^}]*\}', "", content, flags=re.DOTALL)
                await sandbox.write_file(f"{tf_dir}/{filename}", content)

            await aws_service.setup_terraform_state(project["name"], tf_dir)

            # Create tfvars with image URI, app name, ECR repository name, and env vars
            app_name = project["name"].lower()
            ecr_repository_name = f"sirpi/{app_name}"

            tfvars_content = f'''image_uri = "{image_uri}"
app_name = "{app_name}"
ecr_repository_name = "{ecr_repository_name}"
'''
            if env_vars:
                env_vars_tf = "{\n"
                for key, value in env_vars.items():
                    escaped_value = value.replace('"', '\\"')
                    env_vars_tf += f'  "{key}" = "{escaped_value}"\n'
                env_vars_tf += "}"
                tfvars_content += f"app_env_vars = {env_vars_tf}\n"

            await sandbox.write_file(f"{tf_dir}/terraform.tfvars", tfvars_content)

            # Initialize and apply
            await aws_service.terraform_init(tf_dir)
            outputs = await aws_service.terraform_apply(tf_dir, "terraform.tfvars")

        # Store logs
        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="apply",
            logs=collected_logs,
            status="success",
            duration_seconds=int(duration),
        )

        # Update project status and save outputs
        supabase.update_project_deployment_status(project_id, "deployed")

        # Save application URL and Terraform outputs
        if outputs:
            application_url = None
            if "alb_dns_name" in outputs:
                application_url = f"http://{outputs['alb_dns_name']}"

            with supabase.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE projects
                        SET application_url = %s,
                            terraform_outputs = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (application_url, json.dumps(outputs), project_id),
                    )
                    conn.commit()

        # Send completion signal
        await send_completion(project_id)

        return {"success": True, "outputs": outputs, "duration_seconds": int(duration)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Deployment failed: {e}", exc_info=True)

        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="apply",
            logs=[f"Error: {str(e)}"],
            status="failed",
            duration_seconds=int(duration),
        )

        await send_completion(project_id)
        raise HTTPException(status_code=500, detail=f"Deployment failed: {str(e)}")


@router.post("/projects/{project_id}/destroy")
async def destroy_aws_deployment(
    project_id: str, request: Request, user_id: str = Depends(get_current_user_id)
):
    """
    Destroy AWS ECS Fargate infrastructure.
    """
    import time

    start_time = time.time()

    try:
        # Get AWS connection
        aws_data = await get_aws_connection_for_project(project_id, user_id)
        project = aws_data["project"]
        role_arn = aws_data["role_arn"]
        external_id = aws_data["external_id"]

        # Get Terraform files
        gcs = get_gcs_storage()
        owner, repo = project["repository_name"].split("/")
        files = await gcs.get_repository_files(owner, repo)

        tf_files = {f["path"]: f["content"] for f in files if f["path"].endswith(".tf")}

        # Get image URI and env vars (needed for tfvars)
        build_logs = supabase.get_deployment_logs(project_id, "build_image")
        image_uri = "placeholder:latest"  # Fallback
        for log in build_logs.get("logs", []):
            if ".ecr." in log and ".amazonaws.com" in log:
                import re

                match = re.search(
                    r"([0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[^\s:]+:[^\s]+)", log
                )
                if match:
                    image_uri = match.group(1)
                    break

        env_vars = await get_decrypted_env_vars(project_id)

        # Register for SSE streaming
        register_deployment(project_id)
        await send_log(project_id, "Starting infrastructure destruction...")

        # Create sandbox
        sandbox = SandboxManager(template_id=settings.e2b_template_id)
        aws_service = AWSDeploymentService(sandbox, role_arn, external_id)

        # Collect logs
        collected_logs = []

        def collecting_callback(message: str):
            collected_logs.append(message)
            asyncio.create_task(send_log(project_id, message))

        async with sandbox:
            sandbox.set_log_callback(collecting_callback)

            # Configure AWS credentials
            await aws_service._configure_aws_credentials()

            # Write Terraform files
            tf_dir = "/home/user/terraform"
            await sandbox.run_command(f"mkdir -p {tf_dir}", stream_output=False)

            for filename, content in tf_files.items():
                if 'backend "s3"' in content or 'backend "gcs"' in content:
                    import re

                    content = re.sub(r'backend\s+"[^"]+"\s*\{[^}]*\}', "", content, flags=re.DOTALL)
                await sandbox.write_file(f"{tf_dir}/{filename}", content)

            await aws_service.setup_terraform_state(project["name"], tf_dir)

            # Create tfvars (needed for destroy) with image URI, app name, ECR repository name, and env vars
            app_name = project["name"].lower()
            ecr_repository_name = f"sirpi/{app_name}"

            tfvars_content = f'''image_uri = "{image_uri}"
app_name = "{app_name}"
ecr_repository_name = "{ecr_repository_name}"
'''
            if env_vars:
                env_vars_tf = "{\n"
                for key, value in env_vars.items():
                    escaped_value = value.replace('"', '\\"')
                    env_vars_tf += f'  "{key}" = "{escaped_value}"\n'
                env_vars_tf += "}"
                tfvars_content += f"app_env_vars = {env_vars_tf}\n"

            await sandbox.write_file(f"{tf_dir}/terraform.tfvars", tfvars_content)

            # Initialize and destroy
            await aws_service.terraform_init(tf_dir)
            await aws_service.terraform_destroy(tf_dir, "terraform.tfvars")

            # Optional: Cleanup state file
            aws_service.state_manager.cleanup_state(project["name"])

        # Store logs
        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="destroy",
            logs=collected_logs,
            status="success",
            duration_seconds=int(duration),
        )

        # Update project status and clear application URL/outputs
        supabase.update_project_deployment_status(project_id, "destroyed")

        # Clear application URL and Terraform outputs since infrastructure is gone
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects
                    SET application_url = NULL,
                        terraform_outputs = NULL,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (project_id,),
                )
                conn.commit()

        # Send completion signal
        await send_completion(project_id)

        return {"success": True, "duration_seconds": int(duration)}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Destroy failed: {e}", exc_info=True)

        duration = time.time() - start_time
        supabase.save_deployment_logs(
            project_id=project_id,
            operation_type="destroy",
            logs=[f"Error: {str(e)}"],
            status="failed",
            duration_seconds=int(duration),
        )

        await send_completion(project_id)
        raise HTTPException(status_code=500, detail=f"Destroy failed: {str(e)}")
