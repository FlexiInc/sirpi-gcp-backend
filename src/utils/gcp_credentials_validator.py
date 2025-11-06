"""
GCP Credentials Validation Utilities

Checks if user's OAuth credentials are valid and fresh enough for deployment.
Prompts reconnection if expired or missing.
"""

import logging
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

from src.services.supabase import supabase
from src.utils.encryption import decrypt_value as decrypt
from src.core.config import settings

logger = logging.getLogger(__name__)


class CredentialStatus:
    """Credential validation status."""

    VALID = "valid"
    EXPIRED_REFRESHABLE = "expired_refreshable"
    EXPIRED_NOT_REFRESHABLE = "expired_not_refreshable"
    MISSING = "missing"


def _get_gcp_credentials(user_id: str, project_id: str) -> Credentials:
    """Get refreshed GCP credentials for deployment (internal helper)."""
    with supabase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT access_token, refresh_token, token_expiry
                FROM gcp_credentials
                WHERE user_id = %s AND project_id = %s AND status = 'active'
                """,
                (user_id, project_id),
            )
            cred = cur.fetchone()

    if not cred:
        raise ValueError(f"GCP credentials not found for project {project_id}")

    # Decrypt tokens
    from src.utils.encryption import decrypt_value as decrypt

    access_token = decrypt(cred["access_token"])
    refresh_token = decrypt(cred["refresh_token"]) if cred.get("refresh_token") else None

    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.gcp_oauth_client_id,
        client_secret=settings.gcp_oauth_client_secret,
    )

    return credentials


def check_gcp_credentials(user_id: str, project_id: str = None) -> dict:
    """
    Check if user has valid GCP credentials.

    Args:
        user_id: User's Clerk ID
        project_id: Optional specific project to check

    Returns:
        dict with:
            - status: CredentialStatus value
            - needs_reconnect: bool (only true for invalid refresh tokens)
            - message: str (user-friendly message)
            - project_id: str (if found)
    """
    try:
        # Get credentials from database
        if project_id:
            creds_row = supabase.get_gcp_credentials(user_id)
            if not creds_row or creds_row.get("project_id") != project_id:
                return {
                    "status": CredentialStatus.MISSING,
                    "needs_reconnect": False,  # Not a reconnect - first time!
                    "message": "No GCP credentials found. Please connect your GCP account.",
                    "project_id": None,
                }
        else:
            creds_row = supabase.get_gcp_credentials(user_id)
            if not creds_row:
                return {
                    "status": CredentialStatus.MISSING,
                    "needs_reconnect": False,  # Not a reconnect - first time!
                    "message": "No GCP credentials found. Please connect your GCP account.",
                    "project_id": None,
                }

        project_id = creds_row["project_id"]

        # Check token expiry time (if available)
        token_expiry = creds_row.get("token_expiry")

        logger.info(f"Checking credentials for user {user_id}, project {project_id}")
        logger.info(f"Token expiry: {token_expiry}")

        if token_expiry:
            # Parse token expiry (handle both datetime and string)
            if isinstance(token_expiry, str):
                # Parse ISO format string
                from datetime import datetime as dt

                token_expiry = dt.fromisoformat(token_expiry.replace("+00", "+00:00"))

            # Make both timezone-aware for comparison
            from datetime import timezone

            if token_expiry.tzinfo:
                now_utc = datetime.now(timezone.utc)
            else:
                now_utc = datetime.utcnow()

            logger.info(
                f"Token expiry: {token_expiry}, Now: {now_utc}, Expired: {token_expiry <= now_utc}"
            )

            # Check if token is expired
            if token_expiry <= now_utc:
                # Token is expired - try to refresh
                try:
                    credentials = _get_gcp_credentials(user_id, project_id)

                    # Attempt refresh
                    if credentials.refresh_token:
                        credentials.refresh(Request())

                        # Update database with new token
                        from src.utils.encryption import encrypt_value

                        with supabase.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    UPDATE gcp_credentials
                                    SET access_token = %s,
                                        token_expiry = %s,
                                        updated_at = NOW()
                                    WHERE user_id = %s AND project_id = %s
                                    """,
                                    (
                                        encrypt_value(credentials.token),
                                        credentials.expiry,
                                        user_id,
                                        project_id,
                                    ),
                                )

                        logger.info(f"Refreshed expired OAuth token for user {user_id}")

                        return {
                            "status": CredentialStatus.VALID,
                            "needs_reconnect": False,
                            "message": "Credentials refreshed successfully",
                            "project_id": project_id,
                        }
                    else:
                        # No refresh token - need to reconnect
                        return {
                            "status": CredentialStatus.EXPIRED_NOT_REFRESHABLE,
                            "needs_reconnect": True,
                            "message": "Your GCP session has expired. Please reconnect your GCP account.",
                            "project_id": project_id,
                        }

                except RefreshError:
                    # Refresh failed - need to reconnect
                    return {
                        "status": CredentialStatus.EXPIRED_NOT_REFRESHABLE,
                        "needs_reconnect": True,
                        "message": "Your GCP session has expired. Please reconnect your GCP account.",
                        "project_id": project_id,
                    }
                except Exception as e:
                    logger.error(f"Error refreshing token: {e}")
                    return {
                        "status": CredentialStatus.EXPIRED_NOT_REFRESHABLE,
                        "needs_reconnect": True,
                        "message": "Could not refresh credentials. Please reconnect your GCP account.",
                        "project_id": project_id,
                    }

        # Check when credentials were last updated (fallback if no expiry)
        updated_at = creds_row.get("updated_at")

        # If credentials haven't been refreshed in 50 minutes, they're likely expired
        if updated_at:
            # Handle timezone-aware datetime from database
            from datetime import timezone

            if hasattr(updated_at, "tzinfo") and updated_at.tzinfo:
                now_utc = datetime.now(timezone.utc)
            else:
                now_utc = datetime.utcnow()

            time_since_update = now_utc - updated_at

            # If older than 50 minutes, token is likely expired (they expire at 60 mins)
            if time_since_update > timedelta(minutes=50):
                # Try to refresh
                try:
                    credentials = _get_gcp_credentials(user_id, project_id)

                    # Attempt refresh
                    if credentials.expired and credentials.refresh_token:
                        credentials.refresh(Request())

                        # Update database with new token
                        from src.utils.encryption import encrypt_value

                        with supabase.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    UPDATE gcp_credentials
                                    SET access_token = %s,
                                        token_expiry = %s,
                                        updated_at = NOW()
                                    WHERE user_id = %s AND project_id = %s
                                    """,
                                    (
                                        encrypt_value(credentials.token),
                                        credentials.expiry,
                                        user_id,
                                        project_id,
                                    ),
                                )

                        logger.info(
                            f"Refreshed OAuth token for user {user_id}, project {project_id}"
                        )

                        return {
                            "status": CredentialStatus.VALID,
                            "needs_reconnect": False,
                            "message": "Credentials refreshed successfully",
                            "project_id": project_id,
                        }

                    elif not credentials.refresh_token:
                        # No refresh token - need to reconnect
                        return {
                            "status": CredentialStatus.EXPIRED_NOT_REFRESHABLE,
                            "needs_reconnect": True,
                            "message": "Your GCP session has expired. Please reconnect your GCP account.",
                            "project_id": project_id,
                        }

                    else:
                        # Token still valid
                        return {
                            "status": CredentialStatus.VALID,
                            "needs_reconnect": False,
                            "message": "Credentials are valid",
                            "project_id": project_id,
                        }

                except RefreshError:
                    # Refresh token is invalid or revoked
                    return {
                        "status": CredentialStatus.EXPIRED_NOT_REFRESHABLE,
                        "needs_reconnect": True,
                        "message": "Your GCP session has expired. Please reconnect your GCP account.",
                        "project_id": project_id,
                    }

                except Exception as e:
                    logger.error(f"Error checking credentials: {e}")
                    return {
                        "status": CredentialStatus.EXPIRED_NOT_REFRESHABLE,
                        "needs_reconnect": True,
                        "message": "Could not verify GCP credentials. Please reconnect your GCP account.",
                        "project_id": project_id,
                    }

        # Credentials are fresh (updated recently)
        return {
            "status": CredentialStatus.VALID,
            "needs_reconnect": False,
            "message": "Credentials are valid",
            "project_id": project_id,
        }

    except Exception as e:
        logger.error(f"Unexpected error checking credentials: {e}")
        return {
            "status": CredentialStatus.MISSING,
            "needs_reconnect": False,  # Error checking - treat as first time
            "message": "Could not verify credentials. Please connect your GCP account.",
            "project_id": None,
        }
