"""
AWS Deployment Service - Terraform operations for AWS ECS Fargate.
Uses E2B sandbox and cross-account role assumption.
"""

import boto3
import logging
import json
from typing import Dict, Optional
from botocore.exceptions import ClientError

from src.core.config import settings
from src.services.deployment.sandbox_manager import SandboxManager
from src.services.deployment.s3_state_manager import S3StateManager

logger = logging.getLogger(__name__)


class AWSDeploymentService:
    """Service for AWS Terraform deployments."""

    def __init__(self, sandbox: SandboxManager, role_arn: str, external_id: str):
        """
        Initialize AWS deployment service.

        Args:
            sandbox: E2B sandbox manager
            role_arn: IAM role ARN in user's account
            external_id: External ID for role assumption
        """
        self.sandbox = sandbox
        self.role_arn = role_arn
        self.external_id = external_id
        self.state_manager = S3StateManager(role_arn, external_id)
        self._credentials = None

    def _get_credentials(self):
        """Get temporary credentials by assuming user's role."""
        if self._credentials:
            return self._credentials

        sts_client = boto3.client("sts", region_name=settings.aws_region)

        try:
            response = sts_client.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName="sirpi-terraform",
                ExternalId=self.external_id,
                DurationSeconds=3600,
            )

            self._credentials = response["Credentials"]
            logger.info(f"Successfully assumed role for Terraform: {self.role_arn}")
            return self._credentials

        except ClientError as e:
            logger.error(f"Failed to assume role: {e}")
            raise

    async def _configure_aws_credentials(self):
        """Configure AWS credentials in sandbox for Terraform."""
        try:
            creds = self._get_credentials()

            # Write AWS credentials file
            aws_config = f"""[default]
region = {settings.aws_region}
output = json
"""

            aws_credentials = f"""[default]
aws_access_key_id = {creds["AccessKeyId"]}
aws_secret_access_key = {creds["SecretAccessKey"]}
aws_session_token = {creds["SessionToken"]}
"""

            # Create .aws directory
            await self.sandbox.run_command("mkdir -p /home/user/.aws", stream_output=False)

            # Write config files
            await self.sandbox.write_file("/home/user/.aws/config", aws_config)
            await self.sandbox.write_file("/home/user/.aws/credentials", aws_credentials)

            self.sandbox._log("✅ AWS credentials configured for Terraform")

        except Exception as e:
            logger.error(f"Failed to configure AWS credentials: {e}")
            raise

    async def terraform_init(self, tf_dir: str = "/home/user/terraform"):
        """
        Run terraform init.

        Args:
            tf_dir: Directory containing Terraform files
        """
        try:
            self.sandbox._log("$ terraform init")

            result = await self.sandbox.run_command(
                f"cd {tf_dir} && terraform init", stream_output=True, timeout=300
            )

            if result["exit_code"] != 0:
                raise RuntimeError(f"Terraform init failed: {result['stderr']}")

            self.sandbox._log("✅ Terraform initialized successfully")

        except Exception as e:
            logger.error(f"Terraform init failed: {e}")
            raise

    async def terraform_plan(
        self, tf_dir: str = "/home/user/terraform", var_file: Optional[str] = None
    ) -> str:
        """
        Run terraform plan.

        Args:
            tf_dir: Directory containing Terraform files
            var_file: Path to tfvars file

        Returns:
            Plan output
        """
        try:
            cmd = f"cd {tf_dir} && terraform plan"
            if var_file:
                cmd += f" -var-file={var_file}"

            self.sandbox._log(f"$ terraform plan")

            result = await self.sandbox.run_command(cmd, stream_output=True, timeout=300)

            if result["exit_code"] != 0:
                raise RuntimeError(f"Terraform plan failed: {result['stderr']}")

            self.sandbox._log("✅ Terraform plan generated successfully")
            return result["stdout"]

        except Exception as e:
            logger.error(f"Terraform plan failed: {e}")
            raise

    async def terraform_apply(
        self, tf_dir: str = "/home/user/terraform", var_file: Optional[str] = None
    ) -> Dict:
        """
        Run terraform apply.

        Args:
            tf_dir: Directory containing Terraform files
            var_file: Path to tfvars file

        Returns:
            Dict with outputs
        """
        try:
            cmd = f"cd {tf_dir} && terraform apply -auto-approve"
            if var_file:
                cmd += f" -var-file={var_file}"

            self.sandbox._log(f"$ terraform apply -auto-approve")

            result = await self.sandbox.run_command(
                cmd,
                stream_output=True,
                timeout=900,  # 15 minutes for infrastructure deployment
            )

            if result["exit_code"] != 0:
                raise RuntimeError(f"Terraform apply failed: {result['stderr']}")

            self.sandbox._log("✅ Infrastructure deployed successfully")

            # Get outputs
            outputs = await self._get_terraform_outputs(tf_dir)
            return outputs

        except Exception as e:
            logger.error(f"Terraform apply failed: {e}")
            raise

    async def terraform_destroy(
        self, tf_dir: str = "/home/user/terraform", var_file: Optional[str] = None
    ):
        """
        Run terraform destroy.

        Args:
            tf_dir: Directory containing Terraform files
            var_file: Path to tfvars file
        """
        try:
            cmd = f"cd {tf_dir} && terraform destroy -auto-approve"
            if var_file:
                cmd += f" -var-file={var_file}"

            self.sandbox._log(f"$ terraform destroy -auto-approve")

            result = await self.sandbox.run_command(
                cmd,
                stream_output=True,
                timeout=900,  # 15 minutes
            )

            if result["exit_code"] != 0:
                raise RuntimeError(f"Terraform destroy failed: {result['stderr']}")

            self.sandbox._log("✅ Infrastructure destroyed successfully")

        except Exception as e:
            logger.error(f"Terraform destroy failed: {e}")
            raise

    async def _get_terraform_outputs(self, tf_dir: str = "/home/user/terraform") -> Dict:
        """
        Get Terraform outputs as JSON.

        Args:
            tf_dir: Directory containing Terraform files

        Returns:
            Dict of outputs
        """
        try:
            result = await self.sandbox.run_command(
                f"cd {tf_dir} && terraform output -json", stream_output=False, timeout=30
            )

            if result["exit_code"] != 0:
                logger.warning(f"Failed to get outputs: {result['stderr']}")
                return {}

            outputs = json.loads(result["stdout"])

            # Extract just the values
            simplified_outputs = {}
            for key, value in outputs.items():
                if isinstance(value, dict) and "value" in value:
                    simplified_outputs[key] = value["value"]
                else:
                    simplified_outputs[key] = value

            return simplified_outputs

        except Exception as e:
            logger.warning(f"Could not parse Terraform outputs: {e}")
            return {}

    async def setup_terraform_state(self, project_name: str, tf_dir: str = "/home/user/terraform"):
        """
        Configure S3 backend for Terraform state.

        Args:
            project_name: Project name for state key
            tf_dir: Directory containing Terraform files
        """
        try:
            self.sandbox._log("Configuring Terraform state backend...")

            # Generate backend configuration
            backend_config = self.state_manager.configure_backend(project_name)

            # Write backend.tf
            await self.sandbox.write_file(f"{tf_dir}/backend.tf", backend_config)
            self.sandbox._log("✅ Configured S3 backend for Terraform state")

        except Exception as e:
            logger.error(f"Failed to setup Terraform state: {e}")
            raise
