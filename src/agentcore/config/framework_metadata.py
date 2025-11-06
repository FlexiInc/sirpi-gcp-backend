"""
Framework metadata configuration for extensible deployment support.
This file defines framework-specific settings used during Dockerfile generation.

To add a new framework:
1. Add entry to FRAMEWORK_BUILD_OUTPUTS with build directory patterns
2. Add entry to FRAMEWORK_PACKAGE_MANAGERS if it uses a specific tool
3. Update FRAMEWORK_RUNTIME_VERSIONS with supported versions
"""

from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class BuildOutputConfig:
    """Configuration for framework build output."""
    primary_output: str
    fallback_outputs: List[str]
    description: str


@dataclass
class FrameworkMetadata:
    """Complete metadata for a framework."""
    name: str
    language: str
    default_port: int
    default_package_manager: str
    build_outputs: Optional[BuildOutputConfig]
    default_health_path: str
    requires_build: bool


# Framework-specific build output directories
# Used to determine where frontend build artifacts are located
FRAMEWORK_BUILD_OUTPUTS: Dict[str, BuildOutputConfig] = {
    # JavaScript/TypeScript Frameworks
    "next.js": BuildOutputConfig(
        primary_output=".next/standalone",
        fallback_outputs=["out", ".next"],
        description="Next.js can output to standalone or static 'out' directory"
    ),
    "nextjs": BuildOutputConfig(  # Alternative naming
        primary_output=".next/standalone",
        fallback_outputs=["out", ".next"],
        description="Next.js can output to standalone or static 'out' directory"
    ),
    "react": BuildOutputConfig(
        primary_output="build",
        fallback_outputs=["dist"],
        description="Create React App outputs to 'build' directory"
    ),
    "create-react-app": BuildOutputConfig(
        primary_output="build",
        fallback_outputs=["dist"],
        description="Create React App outputs to 'build' directory"
    ),
    "vue": BuildOutputConfig(
        primary_output="dist",
        fallback_outputs=["build"],
        description="Vue CLI outputs to 'dist' directory"
    ),
    "vite": BuildOutputConfig(
        primary_output="dist",
        fallback_outputs=["build"],
        description="Vite outputs to 'dist' directory"
    ),
    "angular": BuildOutputConfig(
        primary_output="dist",
        fallback_outputs=["build"],
        description="Angular CLI outputs to 'dist' directory"
    ),
    "svelte": BuildOutputConfig(
        primary_output="public/build",
        fallback_outputs=["dist", "build"],
        description="SvelteKit outputs to 'public/build' or '.svelte-kit/output'"
    ),
    "sveltekit": BuildOutputConfig(
        primary_output=".svelte-kit/output",
        fallback_outputs=["build", "dist"],
        description="SvelteKit outputs to '.svelte-kit/output'"
    ),
    "nuxt": BuildOutputConfig(
        primary_output=".output/public",
        fallback_outputs=["dist"],
        description="Nuxt 3 outputs to '.output/public'"
    ),
    "gatsby": BuildOutputConfig(
        primary_output="public",
        fallback_outputs=["build"],
        description="Gatsby outputs to 'public' directory"
    ),

    # Backend frameworks don't typically have build outputs for static serving
    "express": BuildOutputConfig(
        primary_output="",
        fallback_outputs=[],
        description="Express doesn't generate static build output"
    ),
    "fastapi": BuildOutputConfig(
        primary_output="",
        fallback_outputs=[],
        description="FastAPI doesn't generate static build output"
    ),
    "django": BuildOutputConfig(
        primary_output="",
        fallback_outputs=[],
        description="Django serves static files from staticfiles/"
    ),
    "flask": BuildOutputConfig(
        primary_output="",
        fallback_outputs=[],
        description="Flask serves static files from static/"
    ),
}


# Complete framework metadata registry
FRAMEWORK_METADATA: Dict[str, FrameworkMetadata] = {
    # Frontend Frameworks
    "next.js": FrameworkMetadata(
        name="Next.js",
        language="JavaScript",
        default_port=3000,
        default_package_manager="npm",
        build_outputs=FRAMEWORK_BUILD_OUTPUTS["next.js"],
        default_health_path="/",
        requires_build=True
    ),
    "react": FrameworkMetadata(
        name="React",
        language="JavaScript",
        default_port=3000,
        default_package_manager="npm",
        build_outputs=FRAMEWORK_BUILD_OUTPUTS["react"],
        default_health_path="/",
        requires_build=True
    ),
    "vue": FrameworkMetadata(
        name="Vue",
        language="JavaScript",
        default_port=8080,
        default_package_manager="npm",
        build_outputs=FRAMEWORK_BUILD_OUTPUTS["vue"],
        default_health_path="/",
        requires_build=True
    ),
    "angular": FrameworkMetadata(
        name="Angular",
        language="TypeScript",
        default_port=4200,
        default_package_manager="npm",
        build_outputs=FRAMEWORK_BUILD_OUTPUTS["angular"],
        default_health_path="/",
        requires_build=True
    ),

    # Backend Frameworks
    "express": FrameworkMetadata(
        name="Express",
        language="JavaScript",
        default_port=3000,
        default_package_manager="npm",
        build_outputs=None,
        default_health_path="/health",
        requires_build=False
    ),
    "fastapi": FrameworkMetadata(
        name="FastAPI",
        language="Python",
        default_port=8000,
        default_package_manager="pip",
        build_outputs=None,
        default_health_path="/health",
        requires_build=False
    ),
    "django": FrameworkMetadata(
        name="Django",
        language="Python",
        default_port=8000,
        default_package_manager="pip",
        build_outputs=None,
        default_health_path="/health",
        requires_build=False
    ),
    "flask": FrameworkMetadata(
        name="Flask",
        language="Python",
        default_port=5000,
        default_package_manager="pip",
        build_outputs=None,
        default_health_path="/health",
        requires_build=False
    ),
}


def get_build_output_path(framework: str, frontend_framework: Optional[str] = None) -> str:
    """
    Get the build output path for a given framework.

    Args:
        framework: Backend framework name (e.g., "fastapi", "express")
        frontend_framework: Optional frontend framework for monorepos (e.g., "next.js", "react")

    Returns:
        Build output path relative to frontend directory

    Examples:
        >>> get_build_output_path("fastapi", "next.js")
        ".next/standalone"
        >>> get_build_output_path("express", "react")
        "build"
    """
    # Normalize framework names (case-insensitive, handle variations)
    target_framework = (frontend_framework or framework).lower().strip()

    # Handle common variations
    framework_aliases = {
        "nextjs": "next.js",
        "cra": "create-react-app",
        "create react app": "create-react-app",
        "vuejs": "vue",
    }
    target_framework = framework_aliases.get(target_framework, target_framework)

    # Look up in registry
    config = FRAMEWORK_BUILD_OUTPUTS.get(target_framework)

    if config and config.primary_output:
        return config.primary_output

    # Fallback to common defaults if not found
    if "next" in target_framework:
        return ".next/standalone"
    elif "react" in target_framework:
        return "build"
    elif "vue" in target_framework or "vite" in target_framework:
        return "dist"

    # Default fallback for unknown frameworks
    return "dist"


def get_framework_metadata(framework: str) -> Optional[FrameworkMetadata]:
    """
    Get complete metadata for a framework.

    Args:
        framework: Framework name (case-insensitive)

    Returns:
        FrameworkMetadata if found, None otherwise
    """
    normalized = framework.lower().strip()
    return FRAMEWORK_METADATA.get(normalized)


def detect_frontend_framework_from_dependencies(dependencies: Dict[str, str]) -> Optional[str]:
    """
    Detect frontend framework from dependencies.
    Used for monorepo scenarios to determine frontend build output.

    Args:
        dependencies: Dictionary of package names to versions

    Returns:
        Detected framework name or None
    """
    dep_lower = {k.lower(): v for k, v in dependencies.items()}

    # Priority-ordered detection
    if "next" in dep_lower:
        return "next.js"
    elif "@angular/core" in dep_lower:
        return "angular"
    elif "vue" in dep_lower:
        return "vue"
    elif "svelte" in dep_lower:
        return "svelte"
    elif "@sveltejs/kit" in dep_lower:
        return "sveltekit"
    elif "nuxt" in dep_lower:
        return "nuxt"
    elif "gatsby" in dep_lower:
        return "gatsby"
    elif "react" in dep_lower:
        return "react"

    return None
