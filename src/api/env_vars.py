"""
Environment Variables API endpoints.
Manages encrypted storage of user's application environment variables.
"""

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File
from pydantic import BaseModel
from typing import List, Dict, Optional
import logging

from src.utils.clerk_auth import get_current_user_id
from src.services.supabase import supabase, DatabaseError

router = APIRouter()
logger = logging.getLogger(__name__)


class EnvVar(BaseModel):
    """Environment variable model."""

    key: str
    value: str
    is_secret: bool = True
    description: Optional[str] = None


class EnvVarResponse(BaseModel):
    """Environment variable response (value hidden for secrets)."""

    key: str
    value: Optional[str] = None  # Only returned if not secret
    is_secret: bool
    description: Optional[str] = None


class EnvVarsUpdateRequest(BaseModel):
    """Bulk update environment variables."""

    env_vars: List[EnvVar]


@router.get("/projects/{project_id}/env-vars", response_model=List[EnvVarResponse])
async def get_project_env_vars(project_id: str, user_id: str = Depends(get_current_user_id)):
    """Get environment variables with decrypted values."""
    try:
        project = supabase.get_project_by_id(project_id)
        if not project or project["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="Project not found")

        env_vars = await get_env_vars_for_project(project_id)

        return [
            EnvVarResponse(
                key=var["key"],
                value=var.get("value"),
                is_secret=var["is_secret"],
                description=var.get("description"),
            )
            for var in env_vars
        ]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get env vars: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve environment variables")


@router.post("/projects/{project_id}/env-vars")
async def save_project_env_vars(
    project_id: str, request: EnvVarsUpdateRequest, user_id: str = Depends(get_current_user_id)
):
    """
    Save or update environment variables for a project.
    Values are encrypted before storage.
    """
    try:
        # Verify project ownership
        project = supabase.get_project_by_id(project_id)
        if not project or project["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="Project not found")

        # Save each env var (will be encrypted)
        for env_var in request.env_vars:
            await save_env_var(
                project_id=project_id,
                key=env_var.key,
                value=env_var.value,
                is_secret=env_var.is_secret,
                description=env_var.description,
            )

        logger.info(f"Saved {len(request.env_vars)} env vars for project {project_id}")

        return {"message": f"Saved {len(request.env_vars)} environment variables"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save env vars: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to save environment variables")


@router.post("/projects/{project_id}/env-vars/upload")
async def upload_env_file(
    project_id: str, file: UploadFile = File(...), user_id: str = Depends(get_current_user_id)
):
    """
    Upload .env file and parse it into environment variables.
    """
    try:
        # Verify project ownership
        project = supabase.get_project_by_id(project_id)
        if not project or project["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="Project not found")

        # Read file content
        content = await file.read()
        env_content = content.decode("utf-8")

        # Parse .env file
        env_vars = parse_env_file(env_content)

        # Save parsed env vars
        for key, value in env_vars.items():
            await save_env_var(
                project_id=project_id,
                key=key,
                value=value,
                is_secret=True,  # Default to secret for uploaded files
            )

        logger.info(f"Uploaded and parsed {len(env_vars)} env vars from file")

        return {
            "message": f"Uploaded {len(env_vars)} environment variables",
            "count": len(env_vars),
            "keys": list(env_vars.keys()),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to upload env file: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to parse environment file")


@router.delete("/projects/{project_id}/env-vars/{key}")
async def delete_env_var(project_id: str, key: str, user_id: str = Depends(get_current_user_id)):
    """Delete an environment variable."""
    try:
        # Verify project ownership
        project = supabase.get_project_by_id(project_id)
        if not project or project["user_id"] != user_id:
            raise HTTPException(status_code=404, detail="Project not found")

        success = await delete_project_env_var(project_id, key)

        if not success:
            raise HTTPException(status_code=404, detail="Environment variable not found")

        return {"message": f"Deleted environment variable: {key}"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete env var: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete environment variable")


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def parse_env_file(content: str) -> Dict[str, str]:
    """
    Parse .env file format.

    Supports:
    - KEY=value
    - KEY="value with spaces"
    - # comments
    - Empty lines
    """
    env_vars = {}

    for line in content.split("\n"):
        line = line.strip()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        # Parse KEY=value
        if "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            env_vars[key] = value

    return env_vars


async def get_env_vars_for_project(project_id: str) -> List[Dict]:
    """Get all env vars for a project from database with decrypted values."""
    from src.utils.encryption import decrypt_value

    try:
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT key, value_encrypted, is_secret, description, created_at
                    FROM project_env_vars
                    WHERE project_id = %s
                    ORDER BY key
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()

                # Decrypt values
                result = []
                for row in rows:
                    env_var = dict(row)
                    encrypted_value = env_var.pop("value_encrypted", None)

                    if encrypted_value:
                        try:
                            env_var["value"] = decrypt_value(encrypted_value)
                        except Exception as e:
                            logger.warning(f"Failed to decrypt env var {env_var['key']}: {e}")
                            env_var["value"] = None
                    else:
                        env_var["value"] = None

                    result.append(env_var)

                return result
    except Exception as e:
        logger.error(f"Failed to get env vars: {e}")
        raise DatabaseError("Failed to retrieve environment variables")


async def save_env_var(
    project_id: str, key: str, value: str, is_secret: bool = True, description: Optional[str] = None
):
    """Save or update an environment variable (encrypted)."""
    from src.utils.encryption import encrypt_value

    encrypted_value = encrypt_value(value)

    try:
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO project_env_vars (project_id, key, value_encrypted, is_secret, description)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, key)
                    DO UPDATE SET
                        value_encrypted = EXCLUDED.value_encrypted,
                        is_secret = EXCLUDED.is_secret,
                        description = EXCLUDED.description,
                        updated_at = NOW()
                    """,
                    (project_id, key, encrypted_value, is_secret, description),
                )
    except Exception as e:
        logger.error(f"Failed to save env var: {e}")
        raise DatabaseError("Failed to save environment variable")


async def delete_project_env_var(project_id: str, key: str) -> bool:
    """Delete an environment variable."""
    try:
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM project_env_vars
                    WHERE project_id = %s AND key = %s
                    RETURNING id
                    """,
                    (project_id, key),
                )
                result = cur.fetchone()
                return bool(result)
    except Exception as e:
        logger.error(f"Failed to delete env var: {e}")
        raise DatabaseError("Failed to delete environment variable")


async def get_decrypted_env_vars(project_id: str) -> Dict[str, str]:
    """
    Get all env vars for deployment (decrypted).
    Used internally for terraform.tfvars generation.
    """
    from src.utils.encryption import decrypt_value

    try:
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT key, value_encrypted
                    FROM project_env_vars
                    WHERE project_id = %s
                    ORDER BY key
                    """,
                    (project_id,),
                )
                rows = cur.fetchall()

                return {row["key"]: decrypt_value(row["value_encrypted"]) for row in rows}
    except Exception as e:
        logger.error(f"Failed to get decrypted env vars: {e}")
        raise DatabaseError("Failed to retrieve environment variables")
