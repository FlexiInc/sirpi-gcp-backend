"""
AWS Fargate template generator.
Implements TemplateGenerator protocol for easy registration.
"""

from typing import Dict
from src.agentcore.templates.registry import TemplateGenerator, TemplateMetadata, DeploymentPlatform
from src.agentcore.models import RepositoryContext


class FargateTemplateGenerator:
    """Generate Terraform for AWS ECS Fargate deployment."""
    
    def generate(
        self,
        analysis_result,  # AnalysisResult from code_analyzer_agent
        project_id: str,
        repo_full_name: str | None = None,
        **kwargs
    ) -> Dict[str, str]:
        """Generate Fargate Terraform files."""
        # Convert AnalysisResult to RepositoryContext for backward compatibility
        from src.agentcore.models import RepositoryContext, DeploymentTarget
        
        context = RepositoryContext(
            language=analysis_result.language,
            framework=analysis_result.framework,
            runtime=analysis_result.runtime_version or analysis_result.language,
            package_manager=analysis_result.package_manager,
            dependencies=analysis_result.dependencies,
            deployment_target=DeploymentTarget.FARGATE,
            ports=[analysis_result.exposed_port] if analysis_result.exposed_port else [8080],
            environment_vars=analysis_result.environment_variables,
            health_check_path=analysis_result.health_check_path,
            start_command=analysis_result.start_command,
            build_command=analysis_result.build_command,
            has_existing_dockerfile=False,
            has_existing_terraform=False
        )
        
        # Import and use existing fargate_template function
        from src.agentcore.templates.terraform.fargate_template import generate_fargate_terraform
        
        return generate_fargate_terraform(
            context=context,
            project_id=project_id,
            repo_full_name=repo_full_name
        )
    
    def get_metadata(self) -> TemplateMetadata:
        """Template metadata for frontend display."""
        return TemplateMetadata(
            name="AWS ECS Fargate",
            platform=DeploymentPlatform.AWS_FARGATE,
            cloud_provider="aws",
            description="Serverless container deployment with auto-scaling and load balancing",
            requires_load_balancer=True,
            requires_container_registry=True,
            supports_autoscaling=True,
            min_cost_estimate_monthly=30.0,  # ~$30/mo for minimal setup
            difficulty="beginner"
        )


# Singleton instance for registry
fargate_template = FargateTemplateGenerator()
