from fastapi import APIRouter, Request, HTTPException
from typing import Dict, Any
import logging
import json

from src.services.supabase import supabase
from src.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, headers: dict, secret: str) -> bool:
    import hmac
    import hashlib
    import base64

    svix_id = headers.get("svix-id")
    svix_timestamp = headers.get("svix-timestamp")
    svix_signature = headers.get("svix-signature")

    if not all([svix_id, svix_timestamp, svix_signature]):
        return False

    signed_content = f"{svix_id}.{svix_timestamp}.{payload.decode()}"
    secret_bytes = base64.b64decode(secret.split("_")[1])
    expected_sig = base64.b64encode(
        hmac.new(secret_bytes, signed_content.encode(), hashlib.sha256).digest()
    ).decode()

    signatures = svix_signature.split(" ")

    for sig in signatures:
        if sig.startswith("v1,") and hmac.compare_digest(sig[3:], expected_sig):
            return True

    return False


@router.post("/webhooks/clerk")
async def clerk_webhook_handler(request: Request):
    headers = {
        "svix-id": request.headers.get("svix-id"),
        "svix-timestamp": request.headers.get("svix-timestamp"),
        "svix-signature": request.headers.get("svix-signature"),
    }

    body = await request.body()

    if not verify_webhook_signature(body, headers, settings.clerk_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("type")
    data = payload.get("data", {})

    logger.info(f"Clerk webhook: {event_type}")

    if event_type == "user.created":
        return await handle_user_created(data)
    elif event_type == "user.updated":
        return await handle_user_updated(data)
    elif event_type == "user.deleted":
        return await handle_user_deleted(data)
    else:
        return {"message": "Event received"}


async def handle_user_created(data: Dict[str, Any]) -> Dict[str, str]:
    user_id = data.get("id")

    email_addresses = data.get("email_addresses", [])
    primary_email_id = data.get("primary_email_address_id")
    email = None

    for email_obj in email_addresses:
        if email_obj.get("id") == primary_email_id:
            email = email_obj.get("email_address")
            break

    if not email and email_addresses:
        email = email_addresses[0].get("email_address")

    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    name = f"{first_name} {last_name}".strip() or None
    image_url = data.get("image_url") or data.get("profile_image_url")

    try:
        supabase.save_user_profile(
            clerk_user_id=user_id,
            email=email or "unknown@example.com",
            name=name,
            avatar_url=image_url,
        )
        logger.info(f"User created: {user_id}")
        return {"success": True, "message": "User created"}
    except Exception as e:
        logger.error(f"User creation error: {type(e).__name__}", exc_info=True)
        return {"success": False, "error": str(e)}


async def handle_user_updated(data: Dict[str, Any]) -> Dict[str, str]:
    user_id = data.get("id")

    email_addresses = data.get("email_addresses", [])
    primary_email_id = data.get("primary_email_address_id")
    email = None

    for email_obj in email_addresses:
        if email_obj.get("id") == primary_email_id:
            email = email_obj.get("email_address")
            break

    if not email and email_addresses:
        email = email_addresses[0].get("email_address")

    first_name = data.get("first_name", "")
    last_name = data.get("last_name", "")
    name = f"{first_name} {last_name}".strip() or None
    image_url = data.get("image_url") or data.get("profile_image_url")

    try:
        supabase.save_user_profile(
            clerk_user_id=user_id,
            email=email or "unknown@example.com",
            name=name,
            avatar_url=image_url,
        )
        logger.info(f"User updated: {user_id}")
        return {"success": True, "message": "User updated"}
    except Exception as e:
        logger.error(f"User update error: {type(e).__name__}", exc_info=True)
        return {"success": False, "error": str(e)}


async def handle_user_deleted(data: Dict[str, Any]) -> Dict[str, str]:
    user_id = data.get("id")
    logger.info(f"User deleted: {user_id}")
    return {"success": True, "message": "User deletion logged"}
