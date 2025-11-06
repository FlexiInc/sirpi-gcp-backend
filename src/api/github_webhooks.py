"""
GitHub Webhooks for PR events.
Handles PR merge detection to trigger auto-deployment.
"""

import logging
import hmac
import hashlib
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from typing import Dict, Any

from src.core.config import settings
from src.services.supabase import get_supabase_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github-webhooks", tags=["github-webhooks"])


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not signature:
        return False

    try:
        hash_algorithm, github_signature = signature.split("=")
    except ValueError:
        return False

    if hash_algorithm != "sha256":
        return False

    mac = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256)
    expected_signature = mac.hexdigest()

    return hmac.compare_digest(expected_signature, github_signature)


async def handle_pr_merged(pr_data: Dict[str, Any]):
    """
    Handle PR merge event - trigger deployment.

    Args:
        pr_data: Pull request data from GitHub webhook
    """
    try:
        logger.info(f"HANDLE_PR_MERGED CALLED with PR data keys: {list(pr_data.keys())}")
        logger.info(f"PR data: number={pr_data.get('number')}, merged={pr_data.get('merged')}")
        supabase = get_supabase_service()

        pr_number = pr_data["number"]
        repo_full_name = pr_data["base"]["repo"]["full_name"]
        merged = pr_data.get("merged", False)

        logger.info(f"Processing PR #{pr_number} from repo {repo_full_name}, merged={merged}")

        if not merged:
            logger.info(f"PR #{pr_number} closed but not merged, skipping deployment")
            return

        logger.info(f"PR #{pr_number} merged in {repo_full_name}, triggering deployment")

        # Find generation by PR number
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                logger.info(
                    f"Searching for generation with pr_number={pr_number}, repo={repo_full_name}"
                )
                cur.execute(
                    """
                    SELECT g.id, g.session_id, g.user_id, p.id as project_id,
                           p.repository_name, p.installation_id
                    FROM generations g
                    JOIN projects p ON g.project_id = p.id
                    WHERE g.pr_number = %s AND p.repository_name = %s
                    ORDER BY g.created_at DESC
                    LIMIT 1
                    """,
                    (pr_number, repo_full_name),
                )
                generation = cur.fetchone()
                logger.info(f"Query result: {generation}")

        logger.info(
            f"Found generation for PR #{pr_number}: {generation is not None} (ID: {generation['id'] if generation else 'None'})"
        )

        if not generation:
            logger.warning(f"No generation found for PR #{pr_number} in {repo_full_name}")
            logger.info(f"Available generations in database for repo {repo_full_name}:")
            # Query to show available generations for debugging
            try:
                with supabase.get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT g.pr_number, p.repository_name, g.pr_merged, g.created_at
                            FROM generations g
                            JOIN projects p ON g.project_id = p.id
                            WHERE p.repository_name = %s
                            ORDER BY g.created_at DESC
                            LIMIT 5
                            """,
                            (repo_full_name,),
                        )
                        available_gens = cur.fetchall()
                        for gen in available_gens:
                            logger.info(
                                f"  - PR #{gen['pr_number']}, merged={gen['pr_merged']}, created={gen['created_at']}"
                            )
            except Exception as debug_error:
                logger.error(f"Error querying available generations: {debug_error}")
            return

        # Update generation to mark PR as merged
        logger.info(f"Updating generation {generation['id']} to mark PR as merged")
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE generations
                    SET pr_merged = true, pr_merged_at = NOW()
                    WHERE id = %s
                    """,
                    (generation["id"],),
                )
                logger.info(f"Updated {cur.rowcount} rows for generation {generation['id']}")

        # Update project status to indicate PR is merged and ready for deployment
        logger.info(f"Updating project {generation['project_id']} status to pr_merged")
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects
                    SET status = 'pr_merged',
                        deployment_status = 'ready_for_deployment',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (generation["project_id"],),
                )
                logger.info(f"Updated {cur.rowcount} rows for project {generation['project_id']}")

        logger.info(
            f"Successfully updated generation {generation['id']} and project {generation['project_id']} - PR merged processed"
        )

    except Exception as e:
        logger.error(f"Failed to handle PR merge: {e}", exc_info=True)
        logger.error(f"PR data that caused error: {pr_data}")


async def execute_cloudformation_deployment(
    cf_service,
    project_id: str,
    generation_id: str,
    owner: str,
    repo: str,
    pr_number: int,
    installation_id: int,
    session_id: str,
):
    """Execute terraform deployment in background."""
    try:
        supabase = get_supabase_service()

        # Update status to deploying
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects
                    SET deployment_status = 'deploying',
                        deployment_started_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (project_id,),
                )

        # Execute CloudFormation deployment
        logger.info(f"Starting CloudFormation deployment for project {project_id}")
        logger.info(f"Deployment parameters: owner={owner}, repo={repo}, session_id={session_id}")

        result = await cf_service.deploy_cloudformation_stackset(
            project_id=project_id,
            generation_id=generation_id,
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            installation_id=installation_id,
            session_id=session_id,
        )

        logger.info(
            f"CloudFormation deployment result: success={result.success}, stack_set_name={result.stack_set_name}"
        )

        # Update final status
        final_status = "deployed" if result.success else "deployment_failed"

        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                if result.success:
                    cur.execute(
                        """
                        UPDATE projects
                        SET deployment_status = %s,
                            cloudformation_stack_set_name = %s,
                            cloudformation_stack_set_id = %s,
                            deployment_completed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (final_status, result.stack_set_name, result.stack_set_id, project_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE projects
                        SET deployment_status = %s,
                            deployment_error = %s,
                            deployment_completed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (final_status, result.error[:500] if result.error else None, project_id),
                    )

        if result.success:
            logger.info(
                f"CloudFormation deployment completed successfully for project {project_id}"
            )
        else:
            logger.error(
                f"CloudFormation deployment failed for project {project_id}: {result.error}"
            )
            # Log deployment errors to help with debugging
            if result.logs:
                logger.error(
                    f"CloudFormation logs: {' | '.join(result.logs[-5:])}"
                )  # Last 5 log entries

    except Exception as e:
        logger.error(f"Deployment execution failed for project {project_id}: {e}", exc_info=True)

        # Update status to failed
        try:
            supabase = get_supabase_service()
            with supabase.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE projects
                        SET deployment_status = 'deployment_failed',
                            deployment_error = %s,
                            deployment_completed_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (str(e)[:500], project_id),
                    )
        except Exception as db_error:
            logger.error(f"Failed to update deployment failure status: {db_error}")


@router.post("/pull-request")
async def github_pr_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    GitHub webhook endpoint for pull request events.

    Handles:
    - PR closed (merged) -> Trigger deployment
    """
    try:
        # Get webhook payload
        payload = await request.body()
        signature = request.headers.get("X-Hub-Signature-256", "")
        event_type = request.headers.get("X-GitHub-Event", "")

        # Verify signature (if webhook secret is configured)
        if settings.github_webhook_secret:
            if not verify_github_signature(payload, signature, settings.github_webhook_secret):
                logger.warning("Invalid GitHub webhook signature")
                raise HTTPException(status_code=401, detail="Invalid signature")

        # Parse JSON payload
        import json

        data = json.loads(payload)

        logger.info(f"Received GitHub webhook: {event_type}")

        # Handle pull_request events
        if event_type == "pull_request" or data.get("pull_request"):
            # Support both header-based and payload-based detection
            action = data.get("action")
            pr_data = data.get("pull_request", {})

            logger.info(f"Processing PR event: action={action}, merged={pr_data.get('merged')}")
            logger.info(f"PR data keys: {list(pr_data.keys())}")
            logger.info(f"PR merged value: {pr_data.get('merged')}")

            if action == "closed" and pr_data.get("merged"):
                logger.info("PR merge detected, calling handle_pr_merged")
                # PR was merged - trigger deployment in background
                background_tasks.add_task(handle_pr_merged, pr_data)
                return {"status": "processing", "message": "Deployment triggered"}

            logger.info(f"PR event ignored: action={action}, merged={pr_data.get('merged')}")
            return {"status": "ignored", "message": f"Action '{action}' not handled"}

        return {"status": "ignored", "message": f"Event '{event_type}' not handled"}

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except Exception as e:
        logger.error(f"Webhook processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Webhook processing failed")


@router.get("/health")
async def webhook_health():
    """Health check for webhook endpoint."""
    return {
        "status": "ok",
        "service": "github-webhooks",
        "endpoints": ["/github-webhooks/pull-request", "/github-webhooks/health"],
        "webhook_secret_configured": settings.github_webhook_secret is not None,
        "supported_events": ["pull_request"],
    }


@router.get("/debug/generations")
async def debug_generations():
    """Debug endpoint to check recent generations and their PR status."""
    try:
        supabase = get_supabase_service()

        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT g.id, g.pr_number, g.pr_merged, g.pr_merged_at, g.created_at,
                           p.repository_name, p.status, p.deployment_status
                    FROM generations g
                    JOIN projects p ON g.project_id = p.id
                    ORDER BY g.created_at DESC
                    LIMIT 10
                    """
                )
                generations = cur.fetchall()

        return {
            "generations": [
                {
                    "id": gen["id"],
                    "pr_number": gen["pr_number"],
                    "pr_merged": gen["pr_merged"],
                    "pr_merged_at": gen["pr_merged_at"],
                    "created_at": gen["created_at"],
                    "repository_name": gen["repository_name"],
                    "project_status": gen["status"],
                    "deployment_status": gen["deployment_status"],
                }
                for gen in generations
            ]
        }
    except Exception as e:
        logger.error(f"Failed to fetch generations for debugging: {e}")
        return {"error": str(e)}
