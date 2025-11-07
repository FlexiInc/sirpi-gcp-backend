from fastapi import APIRouter
from src.models import HealthResponse
from src.core.config import settings
from src.services.supabase import supabase

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    # Check if database is configured
    if settings.database_url:
        db_health = await supabase.health_check()
        db_status = db_health.get("status", "unknown")
    else:
        db_status = "not_configured"

    # Consider app healthy even if optional services aren't configured
    overall_status = "healthy"

    return HealthResponse(
        status=overall_status,
        version="1.0.0",
        environment=settings.environment,
        services={
            "supabase": db_status,
            "bedrock": "not_implemented",
            "dynamodb": "not_implemented",
            "s3": "not_implemented",
        },
    )


@router.get("/health/detailed")
async def detailed_health_check():
    # Check if database is configured
    if settings.database_url:
        db_health = await supabase.health_check()
    else:
        db_health = {"status": "not_configured", "message": "Database credentials not configured"}

    return {
        "status": "healthy",
        "version": "1.0.0",
        "environment": settings.environment,
        "services": {
            "supabase": db_health,
            "gemini": {"status": "configured" if settings.google_cloud_project else "not_configured"},
            "e2b": {"status": "configured" if settings.e2b_api_key else "not_configured"},
            "github": {"status": "configured" if settings.github_app_id else "not_configured"},
        },
        "configuration": {
            "database_port": settings.supabase_port,
            "gcp_region": settings.gcp_cloud_run_region,
            "aws_region": settings.aws_region,
            "gemini_model": settings.gemini_model,
            "default_cloud_provider": settings.default_cloud_provider,
        },
    }
