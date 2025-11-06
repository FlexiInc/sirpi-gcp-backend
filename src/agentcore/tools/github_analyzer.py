"""
GitHub repository analyzer tool.
Fetches and analyzes repository structure and key files.
"""

import logging
from typing import Dict, List, Optional, Tuple
from src.services.github_app import GitHubAppService, GitHubAppError
from src.agentcore.models import RawRepositoryData

logger = logging.getLogger(__name__)


class GitHubAnalyzer:
    """
    Tool to fetch and analyze GitHub repositories.
    Pure Python tool - NOT a Bedrock agent.
    """

    PACKAGE_FILES = {
        "javascript": ["package.json", "package-lock.json", "yarn.lock"],
        "typescript": ["package.json", "tsconfig.json"],
        "python": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"],
        "go": ["go.mod", "go.sum"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "ruby": ["Gemfile", "Gemfile.lock"],
        "php": ["composer.json", "composer.lock"],
    }

    CONFIG_FILES = ["Dockerfile", "docker-compose.yml", ".env.example", "README.md"]

    def __init__(self, github_service: GitHubAppService):
        self.github = github_service

    async def analyze_repository(
        self, installation_id: int, owner: str, repo: str, progress_callback=None
    ) -> RawRepositoryData:
        """
        Fetch and analyze repository structure.

        Args:
            progress_callback: Optional callback function(message: str) to report progress

        Returns:
            RawRepositoryData with file tree and key file contents
        """
        logger.info(f"Analyzing repository: {owner}/{repo}")
        self.progress_callback = progress_callback  # Store for use in other methods

        try:
            file_tree = await self.github.get_repository_contents(
                installation_id, owner, repo, path=""
            )

            # Detect monorepo structure
            subdirectory = self._detect_monorepo_subdirectory(file_tree)
            if subdirectory:
                logger.info(f"Detected monorepo, analyzing subdirectory: {subdirectory}")
                if self.progress_callback:
                    self.progress_callback(
                        f"Detected monorepo structure, analyzing {subdirectory}/"
                    )

                # Re-fetch file tree from subdirectory
                file_tree = await self.github.get_repository_contents(
                    installation_id, owner, repo, path=subdirectory
                )
                base_path = subdirectory
            else:
                base_path = ""

            detected_language = self._detect_language_from_tree(file_tree)
            logger.info(f"Detected language: {detected_language}")

            package_files = await self._fetch_package_files(
                installation_id, owner, repo, detected_language, base_path
            )

            # For monorepos, also fetch frontend/package.json to detect frontend framework
            if subdirectory:
                try:
                    frontend_package = await self.github.read_file(
                        installation_id, owner, repo, "frontend/package.json"
                    )
                    package_files["frontend_package.json"] = frontend_package
                    logger.info("Fetched frontend/package.json for monorepo frontend framework detection")
                    if self.progress_callback:
                        self.progress_callback("Detected frontend dependencies")
                except GitHubAppError:
                    logger.info("No frontend/package.json found in monorepo")

            config_files = await self._fetch_config_files(installation_id, owner, repo, base_path)

            # Detect existing infrastructure files
            (
                existing_dockerfile,
                existing_terraform,
                terraform_location,
            ) = await self._fetch_existing_infrastructure(installation_id, owner, repo, file_tree)

            return RawRepositoryData(
                owner=owner,
                repo=repo,
                files=file_tree,
                package_files=package_files,
                config_files=config_files,
                detected_language=detected_language,
                is_monorepo=subdirectory is not None,
                monorepo_subdirectory=subdirectory,
                existing_dockerfile=existing_dockerfile,
                existing_terraform=existing_terraform,
                terraform_location=terraform_location,
            )

        except GitHubAppError as e:
            logger.error(f"GitHub API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Repository analysis failed: {e}", exc_info=True)
            raise

    def _detect_monorepo_subdirectory(self, file_tree: List[Dict]) -> Optional[str]:
        """
        Detect if this is a monorepo with backend/frontend structure.
        Returns the subdirectory to analyze.

        For demo purposes: Defaults to backend, but skips detection if not a monorepo.
        Future: Let user choose which service to deploy.
        """
        directories = [f.get("name") for f in file_tree if f.get("type") == "dir"]

        # Check for common monorepo patterns
        has_backend = "backend" in directories
        has_frontend = "frontend" in directories
        has_server = "server" in directories
        has_client = "client" in directories

        # If we have both backend and frontend, default to backend
        # (User can import repo twice with different names to deploy both)
        if has_backend and (has_frontend or has_client):
            logger.info("Detected monorepo with backend/ and frontend/ - analyzing backend/")
            logger.info(
                "To deploy frontend separately, import the repo again as a separate project"
            )
            return "backend"

        # If we have server and client, prefer server
        if has_server and has_client:
            return "server"

        # Check for apps/ or packages/ (common in monorepos)
        if "apps" in directories or "packages" in directories:
            logger.warning("Detected apps/packages monorepo structure - not yet supported")
            return None

        return None

    def _detect_language_from_tree(self, file_tree: List[Dict]) -> Optional[str]:
        """Detect primary language from file extensions."""

        extension_counts = {}

        language_extensions = {
            "python": [".py"],
            "javascript": [".js", ".jsx"],
            "typescript": [".ts", ".tsx"],
            "go": [".go"],
            "java": [".java"],
            "ruby": [".rb"],
            "php": [".php"],
        }

        for file in file_tree:
            if file.get("type") != "file":
                continue

            name = file.get("name", "")

            for lang, extensions in language_extensions.items():
                if any(name.endswith(ext) for ext in extensions):
                    extension_counts[lang] = extension_counts.get(lang, 0) + 1

        if not extension_counts:
            return None

        return max(extension_counts, key=extension_counts.get)

    async def _fetch_package_files(
        self,
        installation_id: int,
        owner: str,
        repo: str,
        detected_language: Optional[str],
        base_path: str = "",
    ) -> Dict[str, str]:
        """Fetch package manager files based on detected language."""

        package_files = {}

        if not detected_language:
            return package_files

        files_to_fetch = self.PACKAGE_FILES.get(detected_language, [])

        if detected_language in ["javascript", "typescript"]:
            files_to_fetch.extend(self.PACKAGE_FILES.get("javascript", []))

        for filename in files_to_fetch:
            try:
                file_path = f"{base_path}/{filename}" if base_path else filename
                content = await self.github.read_file(installation_id, owner, repo, file_path)
                package_files[filename] = content
                logger.info(f"Fetched: {file_path}")
                if self.progress_callback:
                    self.progress_callback(f"Fetched dependency file: {filename}")
            except GitHubAppError:
                pass

        return package_files

    async def _fetch_config_files(
        self, installation_id: int, owner: str, repo: str, base_path: str = ""
    ) -> Dict[str, str]:
        """Fetch common configuration files."""

        config_files = {}

        for filename in self.CONFIG_FILES:
            try:
                file_path = f"{base_path}/{filename}" if base_path else filename
                content = await self.github.read_file(installation_id, owner, repo, file_path)
                config_files[filename] = content
                logger.info(f"Fetched config: {file_path}")
                if self.progress_callback:
                    self.progress_callback(f"Fetched configuration: {filename}")
            except GitHubAppError:
                pass

        return config_files

    async def _fetch_existing_infrastructure(
        self, installation_id: int, owner: str, repo: str, file_tree: List[Dict]
    ) -> Tuple[Optional[str], Dict[str, str], Optional[str]]:
        """
        Detect and fetch existing infrastructure files.
        For Dockerfiles: Intelligently select the main application Dockerfile.

        Returns:
            (dockerfile_content, terraform_files_dict, terraform_location)
        """
        existing_dockerfile = None
        existing_terraform = {}
        terraform_location = None
        dockerfile_location = None

        # SMART DOCKERFILE SELECTION - Priority order
        dockerfile_candidates = [
            "Dockerfile",  # 1. Root (highest priority)
            ".docker/Dockerfile",  # 2. Hidden .docker directory (Ghost pattern)
            "docker/Dockerfile",  # 3. Common docker/ directory
            f"{repo}/Dockerfile",  # 4. Directory matching repo name (e.g., n8n/Dockerfile)
            f"docker/{repo}/Dockerfile",  # 5. docker/<repo-name>/Dockerfile
            f"docker/images/{repo}/Dockerfile",  # 6. docker/images/<repo-name>/Dockerfile
            "app/Dockerfile",  # 7. app/ directory
            "docker/app/Dockerfile",  # 8. docker/app/ directory
        ]
        
        # Try each candidate in priority order
        for candidate_path in dockerfile_candidates:
            try:
                existing_dockerfile = await self.github.read_file(
                    installation_id, owner, repo, candidate_path
                )
                dockerfile_location = candidate_path
                logger.info(f"Found Dockerfile at: {candidate_path}")
                break  # Stop at first match
            except GitHubAppError:
                continue
        
        # If no priority match, search all directories (but skip base/test/dev)
        if not existing_dockerfile:
            logger.info("No Dockerfile found in common locations, searching all directories...")
            all_dockerfiles = await self._find_all_dockerfiles(
                installation_id, owner, repo, file_tree
            )
            
            if all_dockerfiles:
                # Filter out base images, test, dev, etc.
                filtered = [
                    path
                    for path in all_dockerfiles
                    if not any(
                        exclude in path.lower()
                        for exclude in ["base", "test", "dev", "example", "sample", "demo"]
                    )
                ]
                
                if filtered:
                    # Prefer paths with repo name in them
                    for path in filtered:
                        if repo.lower() in path.lower():
                            dockerfile_location = path
                            break
                    
                    # Otherwise use first filtered result
                    if not dockerfile_location:
                        dockerfile_location = filtered[0]
                    
                    try:
                        existing_dockerfile = await self.github.read_file(
                            installation_id, owner, repo, dockerfile_location
                        )
                        logger.info(
                            f"Selected Dockerfile: {dockerfile_location} (from {len(all_dockerfiles)} found)"
                        )
                    except GitHubAppError:
                        pass

        # Check for terraform/ directory
        has_terraform_dir = any(
            f.get("type") == "dir" and f.get("name") == "terraform" for f in file_tree
        )

        if has_terraform_dir:
            logger.info("Found terraform/ directory")
            terraform_location = "terraform/"
            try:
                terraform_files = await self.github.get_repository_contents(
                    installation_id, owner, repo, path="terraform"
                )

                for file in terraform_files:
                    if file.get("type") == "file" and file.get("name", "").endswith(".tf"):
                        try:
                            content = await self.github.read_file(
                                installation_id, owner, repo, f"terraform/{file['name']}"
                            )
                            existing_terraform[file["name"]] = content
                            logger.info(f"Found existing Terraform file: terraform/{file['name']}")
                        except GitHubAppError:
                            pass
            except GitHubAppError:
                pass

        # Check for .tf files in root or any location
        if not existing_terraform:
            tf_files = [
                f
                for f in file_tree
                if f.get("type") == "file" and f.get("name", "").endswith(".tf")
            ]

            if tf_files:
                terraform_location = "root"
                logger.info(f"Found {len(tf_files)} .tf files in root")

                for file in tf_files:
                    filename = file["name"]
                    try:
                        content = await self.github.read_file(
                            installation_id, owner, repo, filename
                        )
                        existing_terraform[filename] = content
                        logger.info(f"Found existing Terraform file: {filename}")
                    except GitHubAppError:
                        pass

        return existing_dockerfile, existing_terraform, terraform_location

    async def _find_all_dockerfiles(
        self, installation_id: int, owner: str, repo: str, file_tree: List[Dict]
    ) -> List[str]:
        """
        Search for Dockerfiles in likely locations only.
        Avoids deep recursion through packages/, tests/, etc.
        Returns list of paths like ['docker/images/n8n/Dockerfile'].
        """
        dockerfiles = []
        
        # Directories to search (limited depth)
        search_paths = [
            ".docker",  # Hidden docker directory (Ghost)
            "docker",  # Most common
            "docker/images",  # n8n pattern
            "docker/app",  # Some projects
            "app",  # App directory
            "src",  # Source directory (rarely has Dockerfile but check)
        ]
        
        # Directories to NEVER search (massive, irrelevant)
        excluded_dirs = [
            "node_modules",
            ".git",
            "dist",
            "build",
            ".next",
            "__pycache__",
            "packages",  # Monorepo packages ❌
            "cypress",  # Tests ❌
            ".github",  # CI/CD configs ❌
            "test",
            "tests",  # Test directories ❌
            ".vscode",  # Editor configs ❌
            "coverage",  # Test coverage ❌
            "docs",  # Documentation ❌
        ]
        
        async def search_shallow(path: str, max_depth: int = 2, current_depth: int = 0) -> None:
            """Search directory with depth limit to avoid deep recursion."""
            if current_depth >= max_depth:
                return
            
            try:
                contents = await self.github.get_repository_contents(
                    installation_id, owner, repo, path=path
                )
                
                for item in contents:
                    item_name = item.get("name", "")
                    item_path = f"{path}/{item_name}" if path else item_name
                    
                    # Skip excluded directories
                    if item_name in excluded_dirs:
                        continue
                    
                    if item.get("type") == "file" and item_name == "Dockerfile":
                        dockerfiles.append(item_path)
                    elif item.get("type") == "dir" and current_depth < max_depth - 1:
                        await search_shallow(item_path, max_depth, current_depth + 1)
            except Exception:
                pass  # Skip directories we can't access
        
        # Search specific paths only (limited depth)
        for search_path in search_paths:
            await search_shallow(search_path, max_depth=2)
        
        return dockerfiles


def parse_github_url(url: str) -> Tuple[str, str]:
    """
    Parse GitHub URL to extract owner and repo.

    Examples:
        https://github.com/owner/repo -> (owner, repo)
        https://github.com/owner/repo.git -> (owner, repo)
    """
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]

    parts = url.split("/")
    owner = parts[-2]
    repo = parts[-1]

    return owner, repo
