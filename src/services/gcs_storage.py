"""
Google Cloud Storage service for artifact storage.
Replaces S3 storage for generated files (Dockerfiles, Terraform, etc.).
"""

import logging
from typing import Optional, List, Dict, Any
from google.cloud import storage
from src.core.config import settings


logger = logging.getLogger(__name__)


class GCSStorageService:
    """Service for storing generated artifacts in Google Cloud Storage."""
    
    def __init__(self):
        """Initialize GCS client."""
        self.client = storage.Client(project=settings.google_cloud_project)
        self.bucket_name = settings.gcs_bucket_name
        self.bucket = None
        
        # Ensure bucket exists
        self._ensure_bucket()
    
    def _ensure_bucket(self):
        """Create bucket if it doesn't exist."""
        try:
            self.bucket = self.client.get_bucket(self.bucket_name)
            logger.info(f"Using existing GCS bucket: {self.bucket_name}")
        except Exception:
            logger.info(f"Creating GCS bucket: {self.bucket_name}")
            self.bucket = self.client.create_bucket(
                self.bucket_name,
                location=settings.gcs_bucket_region
            )
    
    def upload_file(
        self,
        content: str,
        file_path: str,
        content_type: str = "text/plain"
    ) -> str:
        """
        Upload file content to GCS.
        
        Args:
            content: File content as string
            file_path: Path within bucket (e.g., "projects/123/Dockerfile")
            content_type: MIME type
            
        Returns:
            Public URL to the file
        """
        blob = self.bucket.blob(file_path)
        blob.upload_from_string(content, content_type=content_type)
        
        logger.info(f"Uploaded to GCS: gs://{self.bucket_name}/{file_path}")
        
        return f"gs://{self.bucket_name}/{file_path}"
    
    def download_file(self, file_path: str) -> Optional[str]:
        """
        Download file content from GCS.
        
        Args:
            file_path: Path within bucket
            
        Returns:
            File content as string, or None if not found
        """
        try:
            blob = self.bucket.blob(file_path)
            content = blob.download_as_text()
            return content
        except Exception as e:
            logger.warning(f"Failed to download {file_path}: {e}")
            return None
    
    def delete_file(self, file_path: str) -> bool:
        """
        Delete file from GCS.
        
        Args:
            file_path: Path within bucket
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            blob = self.bucket.blob(file_path)
            blob.delete()
            logger.info(f"Deleted from GCS: {file_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete {file_path}: {e}")
            return False
    
    def delete_repository_files(self, owner: str, repo: str) -> int:
        """
        Delete all files for a repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Number of files deleted
        """
        prefix = f"{owner}/{repo}/"
        blobs = list(self.bucket.list_blobs(prefix=prefix))
        count = 0
        
        for blob in blobs:
            try:
                blob.delete()
                count += 1
                logger.debug(f"Deleted old file: {blob.name}")
            except Exception as e:
                logger.warning(f"Failed to delete {blob.name}: {e}")
        
        if count > 0:
            logger.info(f"Deleted {count} old files from {prefix}")
        
        return count
    
    def list_files(self, prefix: str) -> list[str]:
        """
        List files with given prefix.
        
        Args:
            prefix: Prefix to filter (e.g., "projects/123/")
            
        Returns:
            List of file paths
        """
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
        return [blob.name for blob in blobs]
    
    def get_signed_url(self, file_path: str, expiration_minutes: int = 60) -> str:
        """
        Generate signed URL for temporary public access.
        
        Args:
            file_path: Path within bucket
            expiration_minutes: URL validity duration
            
        Returns:
            Signed URL
        """
        from datetime import timedelta
        
        blob = self.bucket.blob(file_path)
        url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=expiration_minutes),
            method="GET"
        )
        return url
    
    async def get_repository_files(self, owner: str, repo: str) -> list[dict[str, str]]:
        """Get all files for a repository from GCS."""
        prefix = f"{owner}/{repo}/"
        file_paths = self.list_files(prefix)
        
        files = []
        for file_path in file_paths:
            # Extract just the filename part
            filename = file_path[len(prefix):]
            content = self.download_file(file_path)
            
            if content:
                files.append({
                    "path": filename,
                    "content": content,
                    "description": f"Generated {filename}"
                })
        
        return files
    
    async def get_download_urls(self, gcs_keys: list[str]) -> dict[str, str]:
        """Generate download URLs for multiple files."""
        urls = {}
        for gcs_key in gcs_keys:
            # Extract path from GCS URL
            if gcs_key.startswith("gs://"):
                path = gcs_key.replace(f"gs://{self.bucket_name}/", "")
            else:
                path = gcs_key
            
            # Get just the filename for the key in returned dict
            filename = path.split("/")[-1]
            
            # For now, return the GCS URI directly (no signed URL)
            # TODO: Set up service account for signed URLs
            urls[filename] = gcs_key
        
        return urls


# Singleton instance
_storage_service: Optional[GCSStorageService] = None


def get_gcs_storage() -> GCSStorageService:
    """Get or create singleton GCS storage service."""
    global _storage_service
    
    if _storage_service is None:
        _storage_service = GCSStorageService()
    
    return _storage_service
