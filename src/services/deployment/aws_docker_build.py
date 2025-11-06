"""
AWS Docker Build Service - Builds and pushes Docker images to ECR.
Uses E2B sandbox and cross-account role assumption.
"""

import boto3
import base64
import logging
from typing import Dict, Optional
from botocore.exceptions import ClientError

from src.core.config import settings
from src.services.deployment.sandbox_manager import SandboxManager

logger = logging.getLogger(__name__)


class AWSDockerBuildService:
    """Service for building Docker images and pushing to AWS ECR."""

    def __init__(self, sandbox: SandboxManager, role_arn: str, external_id: str):
        """
        Initialize AWS Docker build service.

        Args:
            sandbox: E2B sandbox manager
            role_arn: IAM role ARN in user's account
            external_id: External ID for role assumption
        """
        self.sandbox = sandbox
        self.role_arn = role_arn
        self.external_id = external_id
        self._credentials = None

    def _get_credentials(self):
        """Get temporary credentials by assuming user's role."""
        if self._credentials:
            return self._credentials

        sts_client = boto3.client("sts", region_name=settings.aws_region)

        try:
            response = sts_client.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName="sirpi-docker-build",
                ExternalId=self.external_id,
                DurationSeconds=3600,
            )

            self._credentials = response["Credentials"]
            logger.info(f"Successfully assumed role for Docker build: {self.role_arn}")
            return self._credentials

        except ClientError as e:
            logger.error(f"Failed to assume role: {e}")
            raise

    def _get_ecr_client(self):
        """Get ECR client with assumed role credentials."""
        creds = self._get_credentials()
        return boto3.client(
            "ecr",
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=settings.aws_ecr_region,
        )

    async def _ensure_ecr_repository(self, repository_name: str) -> str:
        """
        Ensure ECR repository exists in user's account.

        Args:
            repository_name: Name of the ECR repository

        Returns:
            ECR repository URI
        """
        ecr_client = self._get_ecr_client()

        try:
            # Check if repository exists
            response = ecr_client.describe_repositories(repositoryNames=[repository_name])
            repository_uri = response["repositories"][0]["repositoryUri"]
            self.sandbox._log(f"✅ ECR repository exists: {repository_uri}")
            return repository_uri

        except ClientError as e:
            if e.response["Error"]["Code"] == "RepositoryNotFoundException":
                # Create repository
                self.sandbox._log(f"Creating ECR repository: {repository_name}...")
                response = ecr_client.create_repository(
                    repositoryName=repository_name,
                    imageScanningConfiguration={"scanOnPush": True},
                    encryptionConfiguration={"encryptionType": "AES256"},
                )
                repository_uri = response["repository"]["repositoryUri"]
                self.sandbox._log(f"✅ Created ECR repository: {repository_uri}")
                return repository_uri
            else:
                raise

    async def _get_ecr_login_password(self) -> Dict[str, str]:
        """
        Get ECR login credentials.

        Returns:
            Dict with username and password for Docker login
        """
        ecr_client = self._get_ecr_client()

        try:
            response = ecr_client.get_authorization_token()
            auth_data = response["authorizationData"][0]

            # Decode base64 token
            token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
            username, password = token.split(":")

            return {
                "username": username,
                "password": password,
                "registry": auth_data["proxyEndpoint"],
            }

        except Exception as e:
            logger.error(f"Failed to get ECR login: {e}")
            raise

    async def build_and_push_image(
        self, repository_url: str, dockerfile_content: str, image_name: str
    ) -> str:
        """
        Build Docker image and push to ECR.

        Args:
            repository_url: GitHub repository URL
            dockerfile_content: Content of Dockerfile
            image_name: Name for the Docker image (project name)

        Returns:
            Full ECR image URI with tag
        """
        try:
            # Ensure ECR repository exists
            repository_name = f"sirpi/{image_name.lower()}"
            ecr_uri = await self._ensure_ecr_repository(repository_name)
            full_image_uri = f"{ecr_uri}:latest"

            # Clone repository
            self.sandbox._log(f"Cloning {repository_url}...")
            owner_repo = repository_url.replace("https://github.com/", "").rstrip("/")

            await self.sandbox.run_command(
                f"git clone {repository_url} /home/user/repo", stream_output=True
            )
            self.sandbox._log("✅ Repository cloned")

            # Write Dockerfile
            await self.sandbox.write_file("/home/user/repo/Dockerfile", dockerfile_content)
            self.sandbox._log("Wrote Dockerfile")

            # Build Docker image
            self.sandbox._log(f"Building Docker image: {image_name}:latest...")
            image_tag = f"{image_name.lower()}:latest"

            await self.sandbox.build_docker_image(
                dockerfile_path="/home/user/repo/Dockerfile",
                image_name=image_tag,
                context_dir="/home/user/repo",
            )

            # Get ECR login credentials
            self.sandbox._log("Authenticating with ECR...")
            ecr_login = await self._get_ecr_login_password()

            # Login to ECR
            login_result = await self.sandbox.run_command(
                f"echo {ecr_login['password']} | docker login --username {ecr_login['username']} --password-stdin {ecr_login['registry']}",
                stream_output=False,
            )

            if login_result["exit_code"] != 0:
                raise RuntimeError(f"ECR login failed: {login_result['stderr']}")

            self.sandbox._log("✅ ECR authentication successful")

            # Tag image for ECR
            self.sandbox._log(f"Tagging image: {full_image_uri}")
            tag_result = await self.sandbox.run_command(
                f"docker tag {image_tag} {full_image_uri}", stream_output=False
            )

            if tag_result["exit_code"] != 0:
                raise RuntimeError(f"Docker tag failed: {tag_result['stderr']}")

            # Push to ECR
            self.sandbox._log(f"Pushing to ECR: {full_image_uri}")
            push_result = await self.sandbox.run_command(
                f"docker push {full_image_uri}",
                stream_output=True,
                timeout=600,  # 10 minutes for push
            )

            if push_result["exit_code"] != 0:
                raise RuntimeError(f"Docker push failed: {push_result['stderr']}")

            self.sandbox._log(f"✅ Image pushed successfully: {full_image_uri}")

            return full_image_uri

        except Exception as e:
            logger.error(f"Docker build/push failed: {e}")
            self.sandbox._log(f"❌ Build failed: {str(e)}")
            raise
