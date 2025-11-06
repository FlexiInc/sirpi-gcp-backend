"""
Sirpi AI Assistant API - Powered by Amazon Nova Pro.
Chat interface for deployment assistance and terraform configuration.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import logging

from src.services.sirpi_assistant import get_sirpi_assistant
from src.utils.clerk_auth import get_current_user_id
from src.services.supabase import supabase

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    project_id: str
    question: str
    include_logs: bool = True


@router.post("/assistant/chat")
async def chat(
    request: ChatRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Chat with Sirpi AI Assistant (powered by Nova)."""
    try:
        # Verify ownership
        project = supabase.get_project_by_id(request.project_id)
        if not project or project["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get deployment logs
        deployment_logs = None
        application_url = None
        if request.include_logs:
            try:
                # Get deployment logs
                logs = supabase.get_deployment_logs(request.project_id)
                if logs:
                    deployment_logs = []
                    for log_record in logs:
                        if log_record.get("logs"):
                            deployment_logs.extend(log_record["logs"])
                
                # Get application URL from project
                project = supabase.get_project_by_id(request.project_id)
                if project:
                    application_url = project.get("application_url")
            except:
                pass
        
        # Get AgentCore memory from DATABASE (persists beyond session)
        agentcore_memory = None
        try:
            generation = supabase.get_latest_generation_by_project(request.project_id)
            if generation:
                agentcore_memory_id = generation.get("agentcore_memory_id")
                agentcore_memory_arn = generation.get("agentcore_memory_arn")
                generation_session_id = generation.get("session_id")  # Get actual session ID
                
                if agentcore_memory_id and generation_session_id:
                    agentcore_memory = {
                        "id": agentcore_memory_id,
                        "arn": agentcore_memory_arn,
                        "session_id": generation_session_id  # Pass the actual session ID!
                    }
                    logger.info(f"📖 Retrieved AgentCore Memory from database: {agentcore_memory_id}")
                    logger.info(f"   Session ID: {generation_session_id}")
                else:
                    logger.info("No AgentCore Memory ID found in database")
        except Exception as e:
            logger.warning(f"Could not retrieve memory from database: {e}")
        
        # Call assistant
        assistant = get_sirpi_assistant()
        result = await assistant.chat(
            question=request.question,
            project_id=request.project_id,
            deployment_logs=deployment_logs,
            agentcore_memory=agentcore_memory,
            application_url=application_url  # Pass the URL
        )
        
        if result["success"]:
            return {
                "success": True,
                "data": {
                    "answer": result["answer"],
                    "model": result["model"],
                    "agentcore_memory_used": result.get("agentcore_memory_used", False)
                }
            }
        else:
            raise HTTPException(status_code=500, detail=result.get("error"))
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
