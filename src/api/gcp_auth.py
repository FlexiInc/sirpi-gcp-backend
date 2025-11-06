"""GCP OAuth 2.0 authentication endpoints"""
from fastapi import APIRouter, Depends, HTTPException, Query
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import logging
from datetime import datetime, timedelta
from typing import Optional
from psycopg2.extras import Json

from src.core.config import settings
from src.utils.clerk_auth import get_current_user_id
from src.services.supabase import supabase
from src.utils.encryption import encrypt_value as encrypt, decrypt_value as decrypt
from src.utils.gcp_credentials_validator import check_gcp_credentials, CredentialStatus

router = APIRouter(prefix="/gcp", tags=["GCP Auth"])
logger = logging.getLogger(__name__)


def get_oauth_flow():
    """Create OAuth flow instance"""
    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.gcp_oauth_client_id,
                "client_secret": settings.gcp_oauth_client_secret,
                "redirect_uris": [settings.gcp_oauth_redirect_uri],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
        ],
        redirect_uri=settings.gcp_oauth_redirect_uri,
    )


@router.get("/auth/start")
async def start_gcp_auth(user_id: str = Depends(get_current_user_id)):
    """
    Initiate GCP OAuth flow
    Returns authorization URL for user to visit
    """
    try:
        flow = get_oauth_flow()
        
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'  # Force consent to get refresh token
        )
        
        # Store state in database for CSRF protection
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oauth_states (user_id, state, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, state, datetime.utcnow() + timedelta(minutes=10))
                )
        
        logger.info(f"Started GCP OAuth for user {user_id}")
        
        return {
            "authorization_url": authorization_url,
            "state": state
        }
    except Exception as e:
        logger.error(f"Failed to start GCP auth: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/auth/callback")
async def gcp_auth_callback(
    code: str = Query(...),
    state: str = Query(...),
    user_id: str = Depends(get_current_user_id)
):
    """
    Handle OAuth callback from Google
    Exchange authorization code for tokens
    """
    try:
        # Verify state (CSRF protection)
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM oauth_states
                    WHERE user_id = %s AND state = %s
                    """,
                    (user_id, state)
                )
                state_record = cur.fetchone()
        
        if not state_record:
            raise HTTPException(status_code=400, detail="Invalid state parameter")
        
        # Exchange code for tokens
        flow = get_oauth_flow()
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # Fetch user's GCP projects
        service = build('cloudresourcemanager', 'v1', credentials=credentials)
        projects_response = service.projects().list().execute()
        
        user_projects = projects_response.get('projects', [])
        
        if not user_projects:
            raise HTTPException(
                status_code=400,
                detail="No GCP projects found. Please create a GCP project first at https://console.cloud.google.com"
            )
        
        # For now, use the first active project
        # TODO: Later we can let user select which project
        active_projects = [p for p in user_projects if p.get('lifecycleState') == 'ACTIVE']
        
        if not active_projects:
            raise HTTPException(
                status_code=400,
                detail="No active GCP projects found. Please ensure you have an active project."
            )
        
        gcp_project_id = active_projects[0]['projectId']
        
        logger.info(f"Using GCP project: {gcp_project_id} (from {len(active_projects)} available projects)")
        
        # Store OAuth credentials first (for SA creation)
        with supabase.get_connection() as conn:
            with conn.cursor() as cur:
                # Log for debugging
                logger.info(f"Storing credentials - token: {credentials.token[:20]}..., refresh: {bool(credentials.refresh_token)}, expiry: {credentials.expiry}")
                
                # Upsert credentials
                cur.execute(
                    """
                    INSERT INTO gcp_credentials (
                        user_id, project_id, access_token, refresh_token, 
                        token_expiry, status, verified_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id, project_id)
                    DO UPDATE SET
                        access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token,
                        token_expiry = EXCLUDED.token_expiry,
                        status = EXCLUDED.status,
                        verified_at = EXCLUDED.verified_at,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        user_id,
                        gcp_project_id,
                        encrypt(credentials.token),
                        encrypt(credentials.refresh_token) if credentials.refresh_token else None,
                        credentials.expiry if credentials.expiry else None,
                        "active",
                        datetime.utcnow()
                    )
                )
                result = cur.fetchone()
                credential_id = result['id']
                
                # Clean up state
                cur.execute(
                    "DELETE FROM oauth_states WHERE state = %s",
                    (state,)
                )
        
        logger.info(f"OAuth credentials stored for user {user_id}, project {gcp_project_id}")
        logger.info(f"GCP OAuth completed for user {user_id}, project {gcp_project_id}")
        
        return {
            "status": "success",
            "project_id": gcp_project_id,
            "credentials_id": credential_id
        }
        
    except Exception as e:
        logger.error(f"GCP OAuth callback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/credentials")
async def get_user_gcp_credentials(user_id: str = Depends(get_current_user_id)):
    """
    Get user's stored GCP credentials (without sensitive data)
    """
    with supabase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, token_expiry, status, verified_at, created_at
                FROM gcp_credentials
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,)
            )
            credentials = cur.fetchall()
    
    return {"credentials": credentials}


@router.delete("/credentials/{credential_id}")
async def revoke_gcp_credentials(
    credential_id: str,
    user_id: str = Depends(get_current_user_id)
):
    """
    Revoke and delete stored GCP credentials
    """
    with supabase.get_connection() as conn:
        with conn.cursor() as cur:
            # Verify ownership
            cur.execute(
                """
                SELECT id FROM gcp_credentials
                WHERE id = %s AND user_id = %s
                """,
                (credential_id, user_id)
            )
            cred = cur.fetchone()
            
            if not cred:
                raise HTTPException(status_code=404, detail="Credentials not found")
            
            # Delete from database
            cur.execute(
                "DELETE FROM gcp_credentials WHERE id = %s",
                (credential_id,)
            )
    
    logger.info(f"Revoked GCP credentials {credential_id} for user {user_id}")
    
    return {"status": "revoked"}


def get_gcp_credentials(user_id: str, project_id: str) -> Credentials:
    """
    Helper function to get refreshed GCP credentials for deployment
    
    Args:
        user_id: User's Clerk ID
        project_id: GCP project ID
        
    Returns:
        Google OAuth2 Credentials object (refreshed if needed)
    """
    with supabase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT access_token, refresh_token, token_expiry
                FROM gcp_credentials
                WHERE user_id = %s AND project_id = %s AND status = 'active'
                """,
                (user_id, project_id)
            )
            cred = cur.fetchone()
    
    if not cred:
        raise ValueError(f"GCP credentials not found for project {project_id}")
    
    # Decrypt tokens
    access_token = decrypt(cred['access_token'])
    refresh_token = decrypt(cred['refresh_token']) if cred.get('refresh_token') else None
    
    credentials = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.gcp_oauth_client_id,
        client_secret=settings.gcp_oauth_client_secret,
    )
    
    # Refresh if expired
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        
        # Update stored token
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
                        encrypt(credentials.token),
                        credentials.expiry if credentials.expiry else None,
                        user_id,
                        project_id
                    )
                )
        
        logger.info(f"Refreshed OAuth token for project {project_id}")
    
    return credentials



def get_user_gcp_projects(user_id: str) -> list[dict]:
    """
    Get all GCP projects connected for a user
    
    Returns:
        List of {project_id, credentials_id, status}
    """
    with supabase.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, project_id, status
                FROM gcp_credentials
                WHERE user_id = %s AND status = 'active'
                """,
                (user_id,)
            )
            return cur.fetchall()


@router.get("/credentials/status")
async def check_gcp_credentials_status(user_id: str = Depends(get_current_user_id)):
    """
    Check if user's GCP credentials are valid and fresh.
    Frontend calls this before showing deployment buttons.
    
    Returns:
        - valid: bool - credentials are valid and ready to use
        - needs_reconnect: bool - true ONLY if refresh token is invalid (rare)
        - message: str - user-friendly message
        - project_id: str - connected GCP project (if any)
        - status_code: str - 'valid', 'missing', 'expired_not_refreshable'
    """
    try:
        status = check_gcp_credentials(user_id)
        
        # Map internal status to response
        is_valid = status["status"] == CredentialStatus.VALID
        
        return {
            "valid": is_valid,
            "needs_reconnect": status["needs_reconnect"],  # Only true for invalid refresh tokens
            "message": status["message"],
            "project_id": status.get("project_id"),
            "status_code": status["status"]  # 'valid', 'missing', 'expired_not_refreshable'
        }
        
    except Exception as e:
        logger.error(f"Failed to check credentials: {e}")
        return {
            "valid": False,
            "needs_reconnect": False,  # Error checking - treat as missing
            "message": "Could not verify credentials. Please connect your GCP account.",
            "project_id": None,
            "status_code": "missing"
        }
