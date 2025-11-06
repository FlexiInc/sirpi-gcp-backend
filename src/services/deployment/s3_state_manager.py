"""
S3 State Manager for Terraform state in AWS deployments.
Handles S3 bucket creation and DynamoDB table setup in user's AWS account.
"""

import boto3
import logging
from botocore.exceptions import ClientError

from src.core.config import settings

logger = logging.getLogger(__name__)


class S3StateManager:
    """Manages S3 bucket and DynamoDB table for Terraform state."""

    def __init__(self, role_arn: str, external_id: str):
        """
        Initialize with cross-account role credentials.

        Args:
            role_arn: IAM role ARN in user's account
            external_id: External ID for role assumption
        """
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
                RoleSessionName="sirpi-terraform-state",
                ExternalId=self.external_id,
                DurationSeconds=3600,
            )

            self._credentials = response["Credentials"]
            logger.info(f"Successfully assumed role: {self.role_arn}")
            return self._credentials

        except ClientError as e:
            logger.error(f"Failed to assume role: {e}")
            raise

    def _get_s3_client(self):
        """Get S3 client with assumed role credentials."""
        creds = self._get_credentials()
        return boto3.client(
            "s3",
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=settings.s3_region,
        )

    def _get_dynamodb_client(self):
        """Get DynamoDB client with assumed role credentials."""
        creds = self._get_credentials()
        return boto3.client(
            "dynamodb",
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
            region_name=settings.s3_region,
        )

    def ensure_state_bucket(self, project_name: str) -> str:
        """
        Ensure S3 bucket and DynamoDB table exist for Terraform state.
        Uses resources created by CloudFormation stack.

        Args:
            project_name: Project name for state key prefix

        Returns:
            S3 bucket name
        """
        try:
            s3_client = self._get_s3_client()

            # Get AWS account ID from assumed role (using the assumed credentials!)
            creds = self._get_credentials()
            sts_client = boto3.client(
                "sts",
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=settings.aws_region,
            )
            caller_identity = sts_client.get_caller_identity()
            account_id = caller_identity["Account"]

            # Bucket name from CloudFormation template
            bucket_name = f"sirpi-terraform-states-{account_id}"

            # Check if bucket exists
            try:
                s3_client.head_bucket(Bucket=bucket_name)
                logger.info(f"✅ State bucket already exists: {bucket_name}")
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "404":
                    # Bucket doesn't exist - should have been created by CloudFormation
                    logger.error(
                        f"State bucket {bucket_name} not found. CloudFormation stack may not be deployed correctly."
                    )
                    raise Exception(
                        f"State bucket {bucket_name} not found. Please ensure CloudFormation stack was deployed successfully."
                    )
                else:
                    raise

            # Verify DynamoDB lock table exists (optional - just for logging)
            dynamodb_client = self._get_dynamodb_client()
            table_name = settings.dynamodb_terraform_lock_table

            try:
                dynamodb_client.describe_table(TableName=table_name)
                logger.info(f"✅ Lock table exists: {table_name}")
            except ClientError as e:
                # Don't fail if we can't verify - the table might exist but permissions missing
                # Terraform will fail later if the table truly doesn't exist
                logger.warning(f"Could not verify DynamoDB table (may be permissions): {e}")
                logger.info(f"⚠️ Assuming lock table exists: {table_name}")

            logger.info(f"✅ S3 state backend ready: s3://{bucket_name}/projects/{project_name}")
            return bucket_name

        except Exception as e:
            logger.error(f"Failed to ensure state bucket: {e}")
            raise

    def configure_backend(self, project_name: str) -> str:
        """
        Generate Terraform backend configuration for S3.

        Args:
            project_name: Project name for state key

        Returns:
            Terraform backend configuration as string
        """
        bucket_name = self.ensure_state_bucket(project_name)

        backend_config = f'''terraform {{
  backend "s3" {{
    bucket         = "{bucket_name}"
    key            = "projects/{project_name}/terraform.tfstate"
    region         = "{settings.s3_region}"
    dynamodb_table = "{settings.dynamodb_terraform_lock_table}"
    encrypt        = true
  }}
}}
'''

        logger.info(f"Generated S3 backend config for project: {project_name}")
        return backend_config

    def cleanup_state(self, project_name: str):
        """
        Clean up Terraform state for a project (optional cleanup after destroy).

        Args:
            project_name: Project name
        """
        try:
            s3_client = self._get_s3_client()

            # Get account ID (using assumed credentials)
            creds = self._get_credentials()
            sts_client = boto3.client(
                "sts",
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=settings.aws_region,
            )
            caller_identity = sts_client.get_caller_identity()
            account_id = caller_identity["Account"]

            bucket_name = f"sirpi-terraform-states-{account_id}"
            state_key = f"projects/{project_name}/terraform.tfstate"

            # Delete state file
            try:
                s3_client.delete_object(Bucket=bucket_name, Key=state_key)
                logger.info(f"🗑️ Deleted state file: s3://{bucket_name}/{state_key}")
            except ClientError as e:
                logger.warning(f"Could not delete state file: {e}")

            # Delete DynamoDB lock entry to prevent checksum mismatch on next init
            try:
                dynamodb_client = self._get_dynamodb_client()
                table_name = settings.dynamodb_terraform_lock_table
                lock_id = f"{bucket_name}/{state_key}-md5"

                dynamodb_client.delete_item(TableName=table_name, Key={"LockID": {"S": lock_id}})
                logger.info(f"🗑️ Deleted DynamoDB lock entry: {lock_id}")
            except ClientError as e:
                logger.warning(f"Could not delete DynamoDB lock entry: {e}")

            # Note: We don't delete the bucket or DynamoDB table as they're shared
            # across all projects and managed by CloudFormation

        except Exception as e:
            logger.error(f"Failed to cleanup state: {e}")
            # Don't raise - cleanup is optional


async def ensure_s3_state_bucket(role_arn: str, external_id: str, project_name: str) -> str:
    """
    Async wrapper for ensuring S3 state bucket exists.

    Args:
        role_arn: IAM role ARN
        external_id: External ID for role assumption
        project_name: Project name

    Returns:
        S3 bucket name
    """
    manager = S3StateManager(role_arn, external_id)
    return manager.ensure_state_bucket(project_name)
