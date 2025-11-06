from fastapi import APIRouter
from src.models import HealthResponse
from src.core.config import settings
from src.services.supabase import supabase

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    db_health = await supabase.health_check()
    db_status = db_health.get("status", "unknown")
    overall_status = "healthy" if db_status == "healthy" else "degraded"

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
    db_health = await supabase.health_check()

    return {
        "status": "healthy" if db_health.get("status") == "healthy" else "degraded",
        "version": "1.0.0",
        "environment": settings.environment,
        "services": {
            "supabase": db_health,
            "bedrock": {"status": "not_implemented"},
            "dynamodb": {"status": "not_implemented"},
            "s3": {"status": "not_implemented"},
        },
        "configuration": {
            "database_port": settings.supabase_port,
            "aws_region": settings.aws_region,
            "bedrock_model": settings.bedrock_model_id,
        },
    }
