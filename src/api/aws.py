"""
AWS Setup API - CloudFormation magic URLs and connection verification.
"""

from fastapi import APIRouter, HTTPException, Depends
import logging
import secrets
import urllib.parse
from typing import Dict, Any

from src.core.config import settings
from src.services.supabase import supabase, DatabaseError
from src.utils.clerk_auth import get_current_user_id

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/aws/generate-setup-url")
async def generate_cloudformation_url(user_id: str = Depends(get_current_user_id)):
    """
    Generate AWS Console CloudFormation URL for one-click stack creation.
    """
    try:
        # Check if user already has an AWS connection
        existing_connection = supabase.get_aws_connection(user_id)

        if existing_connection and existing_connection.get("external_id"):
            # Reuse existing external ID
            external_id = existing_connection["external_id"]
            logger.info(f"Reusing existing external_id for user {user_id}")
        else:
            # Generate new unique external ID for this user
            external_id = f"sirpi_{user_id}_{secrets.token_urlsafe(16)}"
            logger.info(f"Generated new external_id for user {user_id}: {external_id}")

        # Store/update in database for later verification
        try:
            supabase.save_aws_connection(user_id=user_id, external_id=external_id, status="pending")
        except DatabaseError as e:
            logger.error(f"Failed to save AWS connection: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize AWS connection")

        # Build CloudFormation magic URL
        template_url = settings.cloudformation_template_url

        # Build AWS Console URL using the correct format from AWS docs
        base_url = f"https://{settings.aws_region}.console.aws.amazon.com/cloudformation/home"
        region_param = f"region={settings.aws_region}"
        hash_fragment = "/stacks/create/review"

        params = {
            "templateURL": template_url,
            "stackName": "sirpi-deployment-role",
            "param_ExternalId": external_id,
            "param_SirpiAccountId": settings.aws_account_id,
        }

        query_params = urllib.parse.urlencode(params)

        cloudformation_url = f"{base_url}?{region_param}#{hash_fragment}?{query_params}"

        return {
            "cloudFormationUrl": cloudformation_url,
            "externalId": external_id,
            "message": "CloudFormation URL generated successfully",
        }

    except Exception as e:
        logger.error(f"Failed to generate CloudFormation URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate setup URL")


@router.post("/aws/verify-connection")
async def verify_aws_connection(
    request: Dict[str, str], user_id: str = Depends(get_current_user_id)
):
    """
    Verify AWS connection by attempting to assume the provided role.
    """
    try:
        role_arn = request.get("roleArn")
        project_id = request.get("projectId")  # Optional project ID for linking

        if not role_arn:
            raise HTTPException(status_code=400, detail="Role ARN is required")

        # Get the external ID for this user
        try:
            aws_connection = supabase.get_aws_connection(user_id)
            if not aws_connection or aws_connection.get("external_id") is None:
                raise HTTPException(
                    status_code=400,
                    detail="No AWS connection setup found. Please generate setup URL first.",
                )
        except DatabaseError:
            raise HTTPException(status_code=500, detail="Failed to retrieve AWS connection")

        external_id = aws_connection["external_id"]

        # Test role assumption
        import boto3

        sts_client = boto3.client("sts", region_name=settings.aws_region)

        try:
            response = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName=f"sirpi-verification-{user_id}",
                ExternalId=external_id,
                DurationSeconds=3600,
            )

            # If successful, save the connection
            try:
                supabase.update_aws_connection(
                    user_id=user_id, role_arn=role_arn, status="verified"
                )

                # Get the updated AWS connection to retrieve its ID
                updated_connection = supabase.get_aws_connection(user_id)

                # If project_id is provided, link the AWS connection to the project
                if project_id and updated_connection:
                    try:
                        with supabase.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    UPDATE projects
                                    SET aws_connection_id = %s, deployment_status = 'aws_verified', updated_at = NOW()
                                    WHERE id = %s AND user_id = %s
                                    """,
                                    (updated_connection["id"], project_id, user_id),
                                )
                                conn.commit()
                    except DatabaseError as e:
                        logger.error(f"Failed to link AWS connection to project: {e}")
                        # Don't fail the request if project linking fails

            except DatabaseError as e:
                logger.error(f"Failed to update AWS connection: {e}")
                # Don't fail the request if database update fails

            return {
                "status": "verified",
                "message": "AWS connection verified successfully",
                "roleArn": role_arn,
                "awsConnectionId": updated_connection["id"] if updated_connection else None,
            }

        except Exception as e:
            logger.error(f"Role assumption failed: {e}")
            raise HTTPException(
                status_code=400,
                detail="Failed to assume role. Please check the Role ARN and ensure the CloudFormation stack was created successfully.",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AWS connection verification failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to verify AWS connection")


@router.get("/aws/connection-status")
async def get_aws_connection_status(user_id: str = Depends(get_current_user_id)):
    """
    Get the current AWS connection status for the user.
    """
    try:
        connection = supabase.get_aws_connection(user_id)

        if not connection:
            return {
                "connected": False,
                "status": "not_connected",
                "message": "No AWS connection found",
            }

        return {
            "connected": connection.get("status") == "verified",
            "status": connection.get("status"),
            "roleArn": connection.get("role_arn"),
            "externalId": connection.get("external_id"),
            "message": "AWS connection status retrieved",
        }

    except Exception as e:
        logger.error(f"Failed to get AWS connection status: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve connection status")
