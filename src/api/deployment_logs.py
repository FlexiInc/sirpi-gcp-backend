"""
Server-Sent Events (SSE) for streaming deployment logs.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


# Global dict to store log queues for active deployments
_active_deployments = {}


def register_deployment(deployment_id: str):
    """Register a new deployment for log streaming."""
    if deployment_id not in _active_deployments:
        _active_deployments[deployment_id] = asyncio.Queue()
        logger.info(f"Registered deployment {deployment_id} for log streaming")


def unregister_deployment(deployment_id: str):
    """Unregister a deployment after completion."""
    if deployment_id in _active_deployments:
        del _active_deployments[deployment_id]
        logger.info(f"Unregistered deployment {deployment_id}")


async def send_log(deployment_id: str, log_message: str):
    """Send a log message to the SSE stream."""
    if deployment_id in _active_deployments:
        await _active_deployments[deployment_id].put(log_message)


async def send_completion(deployment_id: str):
    """Send a completion signal to close the stream."""
    if deployment_id in _active_deployments:
        await _active_deployments[deployment_id].put("__STREAM_COMPLETE__")


def get_log_callback(deployment_id: str):
    """Get a callback function for sandbox logging."""

    def callback(message: str):
        # Use asyncio to queue the message
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(send_log(deployment_id, message))
        except RuntimeError:
            # No event loop in current thread, skip
            logger.debug(f"No event loop for log: {message[:50]}")

    return callback


async def log_stream_generator(deployment_id: str) -> AsyncGenerator[str, None]:
    """Generate SSE events from log queue."""
    try:
        # Wait for deployment to be registered
        for _ in range(50):  # Wait up to 5 seconds
            if deployment_id in _active_deployments:
                break
            await asyncio.sleep(0.1)
        else:
            yield f"data: {json.dumps({'error': 'Deployment not found'})}\n\n"
            return
        
        queue = _active_deployments[deployment_id]
        
        # Send initial connection message
        yield f"data: {json.dumps({'type': 'connected', 'message': 'Log stream connected'})}\n\n"
        
        # Stream logs as they come in
        timeout_count = 0
        while True:
            try:
                # Wait for log with timeout
                log_message = await asyncio.wait_for(queue.get(), timeout=1.0)
                
                # Check for completion signal
                if log_message == "__STREAM_COMPLETE__":
                    yield f"data: {json.dumps({'type': 'complete', 'message': 'Stream complete'})}\n\n"
                    break

                # Send log to client
                event_data = {"type": "log", "message": log_message}
                yield f"data: {json.dumps(event_data)}\n\n"
                
                timeout_count = 0  # Reset timeout counter
                
            except asyncio.TimeoutError:
                # Send keepalive
                yield f": keepalive\n\n"
                
                timeout_count += 1
                # After 600 timeouts (10 minutes), close the stream
                # AWS Fargate deployments can take 5+ minutes
                if timeout_count > 600:
                    yield f"data: {json.dumps({'type': 'timeout', 'message': 'Stream timeout'})}\n\n"
                    break
                    
            except Exception as e:
                logger.error(f"Error in log stream: {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break
        
    except Exception as e:
        logger.error(f"Log stream generator error: {e}", exc_info=True)
        yield f"data: {json.dumps({'type': 'error', 'message': 'Stream error'})}\n\n"
    finally:
        # Clean up
        unregister_deployment(deployment_id)


@router.get("/api/v1/gcp/deployment/projects/{project_id}/logs/stream")
async def stream_gcp_deployment_logs(project_id: str):
    """
    SSE endpoint for streaming GCP deployment logs.

    Client should connect to this endpoint to receive real-time logs.
    """
    return StreamingResponse(
        log_stream_generator(project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.get("/api/v1/deployment/projects/{project_id}/logs/stream")
async def stream_aws_deployment_logs(project_id: str):
    """
    SSE endpoint for streaming AWS deployment logs.
    
    Client should connect to this endpoint to receive real-time logs.
    """
    return StreamingResponse(
        log_stream_generator(project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
