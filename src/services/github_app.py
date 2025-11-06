"""
GitHub App service for repository access and automation.
PRODUCTION - Uses GitHub App installation tokens.
"""

import logging
import time
import jwt
import httpx
import asyncio
from pathlib import Path
from typing import Dict, Any, List

from src.core.config import settings

logger = logging.getLogger(__name__)


class GitHubAppError(Exception):
    """Base exception for GitHub App operations."""

    pass


class GitHubAppService:
    """
    GitHub App integration service.
    Handles JWT generation, installation tokens, and repository operations.
    """

    def __init__(self):
        """Initialize GitHub App service."""
        self.app_id = settings.github_app_id
        self.client_id = settings.github_app_client_id
        self.client_secret = settings.github_app_client_secret
        self.webhook_secret = settings.github_app_webhook_secret
        self.github_api_base = settings.github_api_base_url
        self._private_key = None
        self._token_cache: Dict[int, tuple[str, float]] = {}  # installation_id -> (token, expiry)

        logger.info(f"GitHub App initialized: App ID {self.app_id}")

    @property
    def private_key(self) -> str:
        """Lazy load GitHub App private key."""
        if self._private_key is None:
            key_path = Path(settings.github_app_private_key_path)

            if not key_path.exists():
                raise FileNotFoundError(f"GitHub App private key not found at: {key_path}")

            self._private_key = key_path.read_text()
            logger.info("GitHub App private key loaded")

        return self._private_key

    def generate_jwt(self) -> str:
        """
        Generate JWT for GitHub App authentication.
        Valid for 10 minutes.

        Returns:
            JWT token string
        """
        now = int(time.time())

        payload = {
            "iat": now - 60,  # Issued 60 seconds ago (clock skew)
            "exp": now + 600,  # Expires in 10 minutes
            "iss": self.app_id,
        }

        try:
            token = jwt.encode(payload, self.private_key, algorithm="RS256")
            return token
        except Exception as e:
            logger.error(f"Failed to generate JWT: {type(e).__name__}")
            raise GitHubAppError("JWT generation failed")

    async def get_installation_token(self, installation_id: int) -> str:
        """
        Get installation access token for a specific installation.
        Token is valid for 1 hour. Caches token to avoid repeated requests.

        Args:
            installation_id: GitHub App installation ID

        Returns:
            Installation access token
        """
        # Check cache first
        if installation_id in self._token_cache:
            token, expiry = self._token_cache[installation_id]
            # Reuse if token has >5 minutes remaining
            if time.time() < (expiry - 300):
                logger.debug(f"Using cached token for installation {installation_id}")
                return token
        
        jwt_token = self.generate_jwt()

        # Retry with exponential backoff for network issues
        max_retries = 3
        for attempt in range(max_retries):
            try:
                timeout = httpx.Timeout(10.0, connect=5.0)  # Reduce timeout - fail fast
                
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        f"{self.github_api_base}/app/installations/{installation_id}/access_tokens",
                        headers={
                            "Authorization": f"Bearer {jwt_token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                    )

                    if response.status_code != 201:
                        logger.error(f"GitHub API error: {response.status_code}")
                        raise GitHubAppError("Failed to get installation token")

                    data = response.json()
                    token = data["token"]
                    
                    # Cache token (expires in 1 hour)
                    self._token_cache[installation_id] = (token, time.time() + 3600)
                    logger.info(f"Got and cached new token for installation {installation_id}")
                    
                    return token

            except (httpx.RequestError, httpx.TimeoutException) as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(f"GitHub API retry {attempt + 1}/{max_retries} after {wait_time}s: {type(e).__name__}")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"GitHub API failed after {max_retries} retries: {type(e).__name__}")
                    raise GitHubAppError("Network request failed after retries")

    async def get_installation_repositories(self, installation_id: int) -> List[Dict[str, Any]]:
        """
        Get all repositories accessible by this installation.

        Args:
            installation_id: GitHub App installation ID

        Returns:
            List of repository objects
        """
        token = await self.get_installation_token(installation_id)

        timeout = httpx.Timeout(30.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(
                    f"{self.github_api_base}/installation/repositories",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )

                if response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code}")
                    raise GitHubAppError("Failed to get repositories")

                data = response.json()
                return data["repositories"]

            except httpx.RequestError as e:
                logger.error(f"Request error: {type(e).__name__}")
                raise GitHubAppError("Network request failed")

    async def get_repository_contents(
        self, installation_id: int, owner: str, repo: str, path: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Get repository contents at a specific path.

        Args:
            installation_id: GitHub App installation ID
            owner: Repository owner
            repo: Repository name
            path: Path in repository (empty for root)

        Returns:
            List of files/directories
        """
        token = await self.get_installation_token(installation_id)

        timeout = httpx.Timeout(30.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                url = f"{self.github_api_base}/repos/{owner}/{repo}/contents/{path}"

                response = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )

                if response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code}")
                    raise GitHubAppError("Failed to get contents")

                return response.json()

            except httpx.RequestError as e:
                logger.error(f"Request error: {type(e).__name__}")
                raise GitHubAppError("Network request failed")

    async def read_file(self, installation_id: int, owner: str, repo: str, path: str) -> str:
        """
        Read a file's content from repository.
        Returns decoded content.

        Args:
            installation_id: GitHub App installation ID
            owner: Repository owner
            repo: Repository name
            path: File path

        Returns:
            File content as string
        """
        import base64

        token = await self.get_installation_token(installation_id)

        timeout = httpx.Timeout(30.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(
                    f"{self.github_api_base}/repos/{owner}/{repo}/contents/{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )

                if response.status_code != 200:
                    # Don't log 404 as error - it's expected for optional files
                    if response.status_code != 404:
                        logger.error(f"GitHub API error: {response.status_code}")
                    raise GitHubAppError(f"Failed to read file: {response.status_code}")

                data = response.json()

                if data.get("type") != "file":
                    raise GitHubAppError(f"Path is not a file: {path}")

                # Decode base64 content
                content = base64.b64decode(data["content"]).decode("utf-8")
                return content

            except httpx.RequestError as e:
                logger.error(f"Request error: {type(e).__name__}")
                raise GitHubAppError("Network request failed")

    async def create_or_update_file(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
    ) -> Dict[str, Any]:
        """
        Create or update a file in repository.

        Args:
            installation_id: GitHub App installation ID
            owner: Repository owner
            repo: Repository name
            path: File path
            content: File content (will be base64 encoded)
            message: Commit message
            branch: Branch name

        Returns:
            Commit information
        """
        import base64

        token = await self.get_installation_token(installation_id)

        # Encode content
        content_bytes = content.encode("utf-8")
        content_base64 = base64.b64encode(content_bytes).decode("utf-8")

        # Check if file exists (to get SHA if updating)
        sha = None
        timeout = httpx.Timeout(30.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                check_response = await client.get(
                    f"{self.github_api_base}/repos/{owner}/{repo}/contents/{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params={"ref": branch},
                )

                if check_response.status_code == 200:
                    sha = check_response.json()["sha"]
            except httpx.RequestError:
                # File doesn't exist, which is fine for creation
                pass

        # Create or update file
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                payload = {"message": message, "content": content_base64, "branch": branch}

                if sha:
                    payload["sha"] = sha

                response = await client.put(
                    f"{self.github_api_base}/repos/{owner}/{repo}/contents/{path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json=payload,
                )

                if response.status_code not in [200, 201]:
                    logger.error(f"GitHub API error: {response.status_code}")
                    raise GitHubAppError("Failed to create/update file")

                logger.info(f"{'Updated' if sha else 'Created'} file: {path}")
                return response.json()

            except httpx.RequestError as e:
                logger.error(f"Request error: {type(e).__name__}")
                raise GitHubAppError("Network request failed")

    async def create_pull_request(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str = "main",
    ) -> Dict[str, Any]:
        """
        Create a pull request.

        Args:
            installation_id: GitHub App installation ID
            owner: Repository owner
            repo: Repository name
            title: PR title
            body: PR description
            head_branch: Source branch
            base_branch: Target branch

        Returns:
            Pull request object
        """
        token = await self.get_installation_token(installation_id)

        timeout = httpx.Timeout(30.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(
                    f"{self.github_api_base}/repos/{owner}/{repo}/pulls",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    json={"title": title, "body": body, "head": head_branch, "base": base_branch},
                )

                if response.status_code != 201:
                    logger.error(f"GitHub API error: {response.status_code}")
                    raise GitHubAppError("Failed to create pull request")

                pr_data = response.json()
                logger.info(f"Created PR #{pr_data['number']}: {title}")

                return pr_data

            except httpx.RequestError as e:
                logger.error(f"Request error: {type(e).__name__}")
                raise GitHubAppError("Network request failed")

    async def get_pull_request(
        self, installation_id: int, owner: str, repo: str, pr_number: int
    ) -> Dict[str, Any]:
        """
        Get pull request details.

        Args:
            installation_id: GitHub App installation ID
            owner: Repository owner
            repo: Repository name
            pr_number: PR number

        Returns:
            Pull request object with state, merged status, etc.
        """
        token = await self.get_installation_token(installation_id)

        timeout = httpx.Timeout(30.0, connect=10.0)
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.get(
                    f"{self.github_api_base}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )

                if response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code}")
                    raise GitHubAppError("Failed to get pull request")

                return response.json()

            except httpx.RequestError as e:
                logger.error(f"Request error: {type(e).__name__}")
                raise GitHubAppError("Network request failed")


# Lazy initialization - only creates instance when first used
_github_app_instance = None


def get_github_app() -> GitHubAppService:
    """Get GitHub App service instance (lazy singleton)."""
    global _github_app_instance
    if _github_app_instance is None:
        _github_app_instance = GitHubAppService()
    return _github_app_instance
