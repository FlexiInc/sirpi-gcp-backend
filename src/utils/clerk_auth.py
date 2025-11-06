"""
Extract Clerk user ID from JWT token.
"""

import logging
from fastapi import Request, HTTPException
import json
import base64

logger = logging.getLogger(__name__)


async def get_current_user_id(request: Request) -> str:
    """Extract user ID from Clerk Supabase JWT 'sub' claim."""
    auth_header = request.headers.get("authorization")

    if not auth_header:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header format")

    token = auth_header.replace("Bearer ", "")

    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT format")

        payload_part = parts[1]
        padding = 4 - len(payload_part) % 4
        if padding != 4:
            payload_part += "=" * padding

        payload_bytes = base64.urlsafe_b64decode(payload_part)
        payload = json.loads(payload_bytes)

        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("User ID not found in token")

        return user_id

    except ValueError as e:
        logger.error(f"JWT validation error: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logger.error(f"Unexpected error during JWT decode: {e}")
        raise HTTPException(status_code=500, detail="Authentication processing error")
