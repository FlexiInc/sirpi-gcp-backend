"""
GCS State Bucket Management using Python SDK.
Production-ready - uses Application Default Credentials (ADC).
No gcloud CLI required!
"""

import logging
from google.cloud import storage
from google.cloud import service_usage_v1
from google.oauth2.credentials import Credentials
from google.api_core import exceptions
from src.core.config import settings

logger = logging.getLogger(__name__)


async def ensure_gcs_state_bucket(
    credentials: Credentials, gcp_project_id: str, app_name: str, sandbox
) -> str:
    """
    Ensure GCS bucket for Terraform state exists.
    Uses Python SDK with ADC - no gcloud CLI needed!

    Args:
        credentials: OAuth credentials (auto-refreshing)
        gcp_project_id: GCP project ID
        app_name: Application name
        sandbox: E2B sandbox (for logging only)

    Returns:
        Bucket name

    Raises:
        RuntimeError if bucket cannot be created/verified
    """
    bucket_name = f"sirpi-terraform-states-{gcp_project_id}"
    location = settings.gcp_cloud_run_region

    sandbox._log(f"Ensuring Terraform state bucket exists...")

    try:
        # Create Storage client with OAuth credentials
        storage_client = storage.Client(credentials=credentials, project=gcp_project_id)

        # Check if bucket exists
        try:
            bucket = storage_client.get_bucket(bucket_name)
            sandbox._log(f"✅ State bucket already exists: {bucket_name}")
            return bucket_name

        except exceptions.NotFound:
            # Bucket doesn't exist - need to create it
            sandbox._log(f"Bucket not found, will create: {bucket_name}")

        except exceptions.Forbidden as e:
            # Permission issue or API not enabled
            error_str = str(e)

            if "has not been used" in error_str or "SERVICE_DISABLED" in error_str:
                # Storage API not enabled - enable it
                sandbox._log("Storage API not enabled, enabling now...")
                await enable_storage_api(credentials, gcp_project_id, sandbox)

                # Retry bucket check after enabling API
                import asyncio

                await asyncio.sleep(3)

                try:
                    bucket = storage_client.get_bucket(bucket_name)
                    sandbox._log(f"✅ State bucket already exists: {bucket_name}")
                    return bucket_name
                except exceptions.NotFound:
                    pass  # Continue to create bucket
            else:
                # Real permission issue
                raise RuntimeError(f"Permission denied accessing bucket: {error_str[:200]}")

        # Create bucket
        sandbox._log(f"Creating Terraform state bucket: gs://{bucket_name}...")

        bucket = storage.Bucket(storage_client, bucket_name)
        bucket.location = location
        bucket.storage_class = "STANDARD"

        # Security settings
        bucket.iam_configuration.uniform_bucket_level_access_enabled = True
        bucket.iam_configuration.public_access_prevention = "enforced"

        # Enable versioning (safety for state files)
        bucket.versioning_enabled = True

        new_bucket = storage_client.create_bucket(bucket, project=gcp_project_id)

        sandbox._log(f"✅ Created state bucket with versioning: {bucket_name}")

        return bucket_name

    except exceptions.Forbidden as e:
        error_msg = str(e)
        if "has not been used" in error_msg or "SERVICE_DISABLED" in error_msg:
            raise RuntimeError(
                f"Cloud Storage API is not enabled in project {gcp_project_id}. "
                f"Please enable it at: https://console.cloud.google.com/apis/library/storage.googleapis.com?project={gcp_project_id}"
            )
        else:
            raise RuntimeError(f"Permission denied: {error_msg[:200]}")

    except exceptions.AlreadyExists:
        # Race condition - bucket created between check and create (OK!)
        sandbox._log(f"✅ State bucket already exists: {bucket_name}")
        return bucket_name

    except Exception as e:
        logger.error(f"Unexpected error with GCS bucket: {e}", exc_info=True)
        raise RuntimeError(f"GCS state bucket setup failed: {str(e)[:300]}")


async def enable_storage_api(credentials: Credentials, project_id: str, sandbox):
    """
    Enable Cloud Storage API using Python SDK.
    No gcloud CLI needed!
    """
    try:
        sandbox._log("Enabling Cloud Storage API...")

        # Create Service Usage client
        client = service_usage_v1.ServiceUsageClient(credentials=credentials)

        service_name = f"projects/{project_id}/services/storage.googleapis.com"

        # Enable the service
        operation = client.enable_service(name=service_name)

        # Wait for operation to complete (async)
        import asyncio

        await asyncio.to_thread(operation.result, timeout=60)

        sandbox._log("✅ Cloud Storage API enabled")

    except exceptions.AlreadyExists:
        sandbox._log("✅ Cloud Storage API already enabled")

    except Exception as e:
        logger.warning(f"Could not enable Storage API: {e}")
        # Non-fatal - API might already be enabled
        sandbox._log(f"⚠️ Could not auto-enable Storage API: {str(e)[:100]}")


async def cleanup_terraform_state(
    credentials: Credentials, gcp_project_id: str, app_name: str, sandbox
):
    """
    Clean up Terraform state from GCS after successful destroy.
    Uses Python SDK - no gcloud needed!
    """
    bucket_name = f"sirpi-terraform-states-{gcp_project_id}"
    state_prefix = f"projects/{app_name}/"

    try:
        sandbox._log("Cleaning up Terraform state from GCS...")

        # Create Storage client
        storage_client = storage.Client(credentials=credentials, project=gcp_project_id)

        # Get bucket
        try:
            bucket = storage_client.get_bucket(bucket_name)

            # Delete all blobs with prefix
            blobs = bucket.list_blobs(prefix=state_prefix)
            deleted_count = 0

            for blob in blobs:
                blob.delete()
                deleted_count += 1

            if deleted_count > 0:
                sandbox._log(f"✅ Deleted {deleted_count} state file(s) from GCS")
            else:
                sandbox._log("✅ No state files to clean up")

        except exceptions.NotFound:
            sandbox._log("✅ State bucket doesn't exist (already cleaned)")

    except Exception as e:
        # State cleanup failure is non-fatal
        logger.warning(f"State cleanup exception: {e}")
        sandbox._log(f"⚠️ State cleanup failed (non-fatal): {str(e)[:100]}")
