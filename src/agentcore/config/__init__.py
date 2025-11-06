"""
Configuration modules for agentcore.
"""

from .framework_metadata import (
    get_build_output_path,
    get_framework_metadata,
    detect_frontend_framework_from_dependencies,
    FRAMEWORK_METADATA,
    FRAMEWORK_BUILD_OUTPUTS,
)

__all__ = [
    "get_build_output_path",
    "get_framework_metadata",
    "detect_frontend_framework_from_dependencies",
    "FRAMEWORK_METADATA",
    "FRAMEWORK_BUILD_OUTPUTS",
]
