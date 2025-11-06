"""
Session-aware logging handler for streaming logs to frontend.
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional


class SessionLogHandler(logging.Handler):
    """
    Logging handler that captures logs and adds them to active session.
    Allows streaming backend logs to frontend via SSE.
    """

    def __init__(self, session_id: str, active_sessions: Dict[str, Dict[str, Any]]):
        super().__init__()
        self.session_id = session_id
        self.active_sessions = active_sessions
        self.setLevel(logging.INFO)

    def emit(self, record: logging.LogRecord):
        """Add log record to session logs."""
        try:
            if self.session_id not in self.active_sessions:
                return

            # Skip noisy logs
            if record.name in ["httpx", "urllib3", "asyncio"]:
                return

            # Skip system logs - we only want agent logs
            if record.levelno < logging.WARNING:
                return

        except Exception:
            pass


def attach_session_logger(session_id: str, active_sessions: Dict) -> SessionLogHandler:
    """Attach session-aware log handler to root logger."""
    handler = SessionLogHandler(session_id, active_sessions)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    return handler


def detach_session_logger(handler: SessionLogHandler):
    """Remove session log handler."""
    root_logger = logging.getLogger()
    root_logger.removeHandler(handler)
