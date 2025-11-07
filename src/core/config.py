"""
Core configuration for Sirpi Google Cloud Run application.
Cloud-agnostic platform - users can deploy to GCP or AWS.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Literal


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Application Settings
    environment: str = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:3000"

    # Clerk Authentication - Optional (only required for authenticated endpoints)
    clerk_secret_key: str | None = None
    clerk_webhook_secret: str | None = None

    # Supabase Database - Optional (only required for database-backed features)
    supabase_user: str | None = None
    supabase_password: str | None = None
    supabase_host: str | None = None
    supabase_port: int = 6543
    supabase_dbname: str = "postgres"

    # Google Cloud & ADK Configuration - Optional (only required for AI agents)
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = True
    google_api_key: str | None = None  # For local dev with AI Studio

    # ADK Configuration
    adk_app_name: str = "sirpi"
    adk_session_service_type: Literal["database", "inmemory"] = (
        "database"  # Use database for multi-agent collaboration
    )

    # Gemini Model Configuration
    gemini_model: str = "gemini-2.5-flash"
    gemini_temperature: float = 0.7

    # Cloud-Agnostic Deployment
    default_cloud_provider: Literal["gcp", "aws"] = "gcp"

    # GCP-specific (for user deployments)
    gcp_artifact_registry_location: str = "us-central1"
    gcp_artifact_registry_repository: str = "sirpi-deployments"
    gcp_cloud_run_region: str = "us-central1"

    # AWS-specific (for user deployments to AWS)
    aws_region: str = "us-west-2"
    aws_account_id: str | None = None
    aws_ecr_region: str = "us-west-2"

    # AWS Terraform State Management
    s3_terraform_state_bucket: str = "sirpi-terraform-states"
    s3_region: str = "us-west-2"
    dynamodb_terraform_lock_table: str = "sirpi-terraform-locks"

    # AWS CloudFormation Setup
    cloudformation_template_url: str = (
        "https://sirpi-public-assets.s3.amazonaws.com/cloudformation/sirpi-setup.yaml"
    )

    # GitHub App Configuration - Optional (only required for GitHub integration)
    github_app_id: str | None = None
    github_app_client_id: str | None = None
    github_app_client_secret: str | None = None
    github_app_private_key_path: str = "./github-app-private-key.pem"
    github_app_webhook_secret: str | None = None
    github_app_name: str = "sirpi-github-app"
    github_webhook_secret: str | None = None

    # GitHub URLs
    github_base_url: str = "https://github.com"
    github_api_base_url: str = "https://api.github.com"

    # Google Cloud Storage - Optional (only required for file storage features)
    gcs_bucket_name: str = "sirpi-generated-files"
    gcs_bucket_region: str = "us-central1"

    # E2B API Key for sandbox execution - Optional (only required for deployment features)
    e2b_api_key: str | None = None
    e2b_template_id: str | None = None  # Optional custom E2B template

    # GCP OAuth for user deployments
    gcp_oauth_client_id: str = ""
    gcp_oauth_client_secret: str = ""
    gcp_oauth_redirect_uri: str = "http://localhost:3000/api/gcp/callback"

    # Encryption for sensitive data
    encryption_master_key: str | None = None

    @property
    def cors_origins_list(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @property
    def database_url(self) -> str | None:
        """Build database connection string for SQLAlchemy (Supabase)."""
        if not all([self.supabase_user, self.supabase_password, self.supabase_host]):
            return None
        return (
            f"postgresql+psycopg2://{self.supabase_user}:{self.supabase_password}"
            f"@{self.supabase_host}:{self.supabase_port}/{self.supabase_dbname}"
        )

    @property
    def adk_session_db_url(self) -> str | None:
        """
        Build ADK session database URL.
        Uses Supabase with pg8000 driver (required by ADK DatabaseSessionService).
        URL-encodes password to handle special characters like @.
        Returns None if database credentials are not configured.
        """
        if not all([self.supabase_user, self.supabase_password, self.supabase_host]):
            return None

        from urllib.parse import quote_plus

        encoded_password = quote_plus(self.supabase_password)

        return (
            f"postgresql+pg8000://{self.supabase_user}:{encoded_password}"
            f"@{self.supabase_host}:{self.supabase_port}/{self.supabase_dbname}"
        )


settings = Settings()
