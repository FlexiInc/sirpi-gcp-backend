"""
Template registry for multi-cloud deployment templates.
Makes it easy to add new deployment targets without modifying agent code.
"""

from typing import Dict, Protocol, Any
from enum import Enum
from pydantic import BaseModel


class DeploymentPlatform(str, Enum):
    """Supported deployment platforms."""
    AWS_FARGATE = "aws_fargate"
    AWS_EC2 = "aws_ec2"
    AWS_LAMBDA = "aws_lambda"
    GCP_CLOUD_RUN = "gcp_cloud_run"
    GCP_GKE = "gcp_gke"
    AZURE_CONTAINER_APPS = "azure_container_apps"
    KUBERNETES_GENERIC = "kubernetes_generic"


class TemplateMetadata(BaseModel):
    """Metadata for a deployment template."""
    name: str
    platform: DeploymentPlatform
    cloud_provider: str  # "aws", "gcp", "azure"
    description: str
    requires_load_balancer: bool = True
    requires_container_registry: bool = True
    supports_autoscaling: bool = True
    min_cost_estimate_monthly: float  # USD
    difficulty: str  # "beginner", "intermediate", "advanced"


class TemplateGenerator(Protocol):
    """Protocol (interface) that all template generators must implement."""
    
    def generate(
        self,
        analysis_result: Any,
        project_id: str,
        repo_full_name: str | None = None,
        **kwargs
    ) -> Dict[str, str]:
        """
        Generate infrastructure files.
        
        Args:
            analysis_result: Analyzed repository context
            project_id: Unique project identifier
            repo_full_name: Full repo name (owner/repo)
            **kwargs: Additional template-specific parameters
            
        Returns:
            Dict mapping filenames to content
        """
        ...
    
    def get_metadata(self) -> TemplateMetadata:
        """Return template metadata."""
        ...


class TemplateRegistry:
    """
    Central registry for all deployment templates.
    Agents query this instead of hardcoded templates.
    """
    
    _templates: Dict[DeploymentPlatform, TemplateGenerator] = {}
    
    @classmethod
    def register(cls, platform: DeploymentPlatform, generator: TemplateGenerator):
        """Register a template generator."""
        cls._templates[platform] = generator
    
    @classmethod
    def get(cls, platform: DeploymentPlatform) -> TemplateGenerator:
        """Get template generator for platform."""
        # Lazy load templates if not yet registered
        if len(cls._templates) == 0:
            _register_templates()
        
        if platform not in cls._templates:
            raise ValueError(f"No template registered for {platform}")
        return cls._templates[platform]
    
    @classmethod
    def list_available(cls) -> list[TemplateMetadata]:
        """List all available templates with metadata."""
        # Lazy load templates if not yet registered
        if len(cls._templates) == 0:
            _register_templates()
        return [gen.get_metadata() for gen in cls._templates.values()]
    
    @classmethod
    def get_by_cloud(cls, cloud_provider: str) -> list[TemplateMetadata]:
        """Get templates for specific cloud provider."""
        return [
            meta for meta in cls.list_available()
            if meta.cloud_provider == cloud_provider
        ]


# Lazy registration - templates register themselves on import
def _register_templates():
    """Lazy load and register all templates."""
    if len(TemplateRegistry._templates) > 0:
        return  # Already registered
    
    try:
        from src.agentcore.templates.aws import fargate_template, lambda_template
        TemplateRegistry.register(DeploymentPlatform.AWS_FARGATE, fargate_template)
        TemplateRegistry.register(DeploymentPlatform.AWS_LAMBDA, lambda_template)
    except ImportError as e:
        import logging
        logging.warning(f"Failed to import AWS templates: {e}")
    
    try:
        from src.agentcore.templates.gcp import cloud_run_template, gke_template
        TemplateRegistry.register(DeploymentPlatform.GCP_CLOUD_RUN, cloud_run_template)
        TemplateRegistry.register(DeploymentPlatform.GCP_GKE, gke_template)
    except ImportError as e:
        import logging
        logging.warning(f"Failed to import GCP templates: {e}")


# Usage Example:
"""
# In TerraformGeneratorAgent:

async def generate(self, analysis_result, cloud_provider="aws"):
    # Get template from registry
    platform = DeploymentPlatform.AWS_FARGATE
    template = TemplateRegistry.get(platform)
    
    # Generate files
    files = template.generate(
        analysis_result=analysis_result,
        project_id=self.project_id,
        repo_full_name=self.repo_name
    )
    
    return files

# In API/Frontend:

# List available templates for user to choose
templates = TemplateRegistry.get_by_cloud("aws")
for template in templates:
    print(f"{template.name}: ${template.min_cost_estimate_monthly}/mo")
"""
