"""
Cloud-Agnostic Terraform Generator using Template Registry.
Supports multi-cloud deployments via pluggable templates.
"""

import logging
from typing import Dict, Literal

from src.agentcore.templates.registry import TemplateRegistry, DeploymentPlatform
from .code_analyzer_agent import AnalysisResult


CloudProvider = Literal["aws", "gcp"]


class TerraformGeneratorAgent:
    """
    Template-based Terraform generator using registry pattern.
    Easily extensible for new deployment platforms.
    """
    
    def __init__(self, cloud_provider: CloudProvider = "aws"):
        """
        Initialize generator.
        
        Args:
            cloud_provider: Target cloud (aws or gcp)
        """
        self.logger = logging.getLogger(__name__)
        self.cloud_provider = cloud_provider
    
    def generate(
        self,
        repo_url: str,
        analysis: AnalysisResult,
        deployment_platform: str = None,
        gcp_project_id: str = None  # Pass user's GCP project ID for state bucket
    ) -> Dict[str, str]:
        """
        Generate Terraform configuration using template registry.
        
        Args:
            repo_url: GitHub repository URL
            analysis: Repository analysis results
            deployment_platform: Optional specific platform (e.g., "aws_fargate", "aws_lambda", "gcp_cloud_run")
            
        Returns:
            Dictionary of Terraform files
        """
        # Determine which template to use
        if deployment_platform:
            # User specified exact platform
            try:
                platform = DeploymentPlatform(deployment_platform)
            except ValueError:
                self.logger.warning(f"Unknown platform {deployment_platform}, falling back to defaults")
                platform = self._get_default_platform()
        else:
            # Use cloud provider default
            platform = self._get_default_platform()
        
        # Extract repo info
        repo_parts = repo_url.rstrip('/').split('/')
        repo_full_name = f"{repo_parts[-2]}/{repo_parts[-1].replace('.git', '')}" if len(repo_parts) >= 2 else None
        
        self.logger.info(f"Generating Terraform using {platform.value} template")
        
        try:
            # Get template from registry
            template = TemplateRegistry.get(platform)
            
            # Generate files using template
            terraform_files = template.generate(
                analysis_result=analysis,
                project_id="sirpi",  # TODO: Pass actual project_id from orchestrator
                repo_full_name=repo_full_name,
                gcp_project_id=gcp_project_id  # Pass for state bucket naming
            )
            
            self.logger.info(f"Generated {len(terraform_files)} Terraform files")
            return terraform_files
            
        except Exception as e:
            self.logger.error(f"Terraform generation failed: {e}", exc_info=True)
            raise ValueError(f"Failed to generate Terraform: {str(e)}")
    
    def _get_default_platform(self) -> DeploymentPlatform:
        """Get default deployment platform for cloud provider."""
        if self.cloud_provider == "aws":
            return DeploymentPlatform.AWS_FARGATE
        elif self.cloud_provider == "gcp":
            return DeploymentPlatform.GCP_CLOUD_RUN
        else:
            # Fallback
            return DeploymentPlatform.AWS_FARGATE
    
    def list_available_platforms(self, cloud_provider: str = None) -> list:
        """
        List available deployment platforms.
        
        Args:
            cloud_provider: Optional filter by cloud (aws, gcp, azure)
            
        Returns:
            List of TemplateMetadata objects
        """
        if cloud_provider:
            return TemplateRegistry.get_by_cloud(cloud_provider)
        return TemplateRegistry.list_available()
