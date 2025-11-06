"""
Docker Build Service - Builds Docker images from GitHub repositories and pushes to ECR.
"""

import boto3
import json
import logging
import asyncio
from typing import Optional, Dict
from concurrent.futures import ThreadPoolExecutor

from src.core.config import settings

try:
    from e2b_code_interpreter import Sandbox
    E2B_AVAILABLE = True
except ImportError:
    E2B_AVAILABLE = False
    logging.warning("E2B not available")

logger = logging.getLogger(__name__)


class DockerBuildService:
    """Service for building and pushing Docker images using E2B."""

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=2)

    def _add_log_to_session(self, session_id: str, message: str):
        """Add log message to active deployment session."""
        try:
            from src.api.deployments import active_deployment_sessions
            
            if session_id in active_deployment_sessions:
                active_deployment_sessions[session_id]["logs"].append(message)
        except Exception as e:
            logger.error(f"Failed to add log to session: {e}")

    async def _run_blocking_command(self, sandbox, command: str, session_id: str, prefix: str = "", timeout: int = 300):
        """Run blocking command in thread pool."""
        loop = asyncio.get_event_loop()
        
        def run_command():
            return sandbox.commands.run(
                command,
                on_stdout=lambda line: self._add_log_to_session(session_id, f"{prefix}{line.strip()}") if line.strip() else None,
                on_stderr=lambda line: self._add_log_to_session(session_id, f"{prefix}⚠️ {line.strip()}") if line.strip() else None,
                timeout=timeout
            )
        
        result = await loop.run_in_executor(self.executor, run_command)
        return result

    async def build_and_push_image(
        self,
        session_id: str,
        project_id: str,
        repository_url: str,
        role_arn: str,
        external_id: str,
        ecr_repository_url: str,  # This will be ignored, we'll create it dynamically
    ) -> Dict:
        """Build Docker image and push to ECR in user's AWS account."""
        logs = []

        def add_log(message: str):
            logs.append(message)
            self._add_log_to_session(session_id, message)
            logger.info(f"[Docker Build] {message}")

        try:
            if not E2B_AVAILABLE:
                return {"success": False, "error": "E2B not available"}

            add_log("🐳 Starting Docker image build process...")
            
            # Extract repo info
            repo_parts = repository_url.replace("https://github.com/", "").split("/")
            owner, repo = repo_parts[0], repo_parts[1]
            
            # Use GitHub repo name for ECR repository name (sanitized)
            ecr_repo_name = repo.lower().replace("_", "-")
            
            add_log(f"📦 Repository: {owner}/{repo}")
            add_log(f"🏷️  ECR Name: {ecr_repo_name}")

            # Get AWS credentials by assuming user's role
            add_log("🔐 Assuming AWS role in your account...")
            # Lambda uses execution role automatically - no explicit credentials needed
            sts = boto3.client(
                "sts",
                region_name=settings.aws_region
            )
            
            response = sts.assume_role(
                RoleArn=role_arn,
                RoleSessionName=f"sirpi-docker-build-{session_id[:8]}",
                ExternalId=external_id,
                DurationSeconds=3600,
            )
            
            credentials = response["Credentials"]
            add_log("✅ Got AWS credentials")
            
            # Get account ID from the AssumeRole response
            account_id = response["AssumedRoleUser"]["Arn"].split(":")[4]
            
            # Construct ECR repository URL in user's account
            ecr_repository_url = f"{account_id}.dkr.ecr.{settings.aws_region}.amazonaws.com/{ecr_repo_name}"
            add_log(f"📍 Target ECR: {ecr_repository_url}")

            # Create ECR repository if it doesn't exist in user's account
            add_log("🏗️ Ensuring ECR repository exists in your account...")
            try:
                ecr_client = boto3.client(
                    "ecr",
                    aws_access_key_id=credentials["AccessKeyId"],
                    aws_secret_access_key=credentials["SecretAccessKey"],
                    aws_session_token=credentials["SessionToken"],
                    region_name=settings.aws_region
                )
                
                try:
                    ecr_client.describe_repositories(repositoryNames=[ecr_repo_name])
                    add_log(f"✅ ECR repository '{ecr_repo_name}' already exists")
                except ecr_client.exceptions.RepositoryNotFoundException:
                    add_log(f"📦 Creating ECR repository '{ecr_repo_name}'...")
                    ecr_client.create_repository(
                        repositoryName=ecr_repo_name,
                        imageScanningConfiguration={"scanOnPush": True},
                        imageTagMutability="MUTABLE",
                    )
                    add_log(f"✅ ECR repository created successfully")
            except Exception as e:
                add_log(f"❌ Failed to create ECR repository: {str(e)}")
                return {"success": False, "error": f"Failed to create ECR: {str(e)}"}

            # Create E2B sandbox with maximum allowed timeout
            add_log("🏗️ Creating build environment...")
            sandbox = Sandbox.create(
                api_key=settings.e2b_api_key,
                timeout=3600  # 60 minutes (E2B maximum allowed)
            )
            add_log("✅ Build environment ready")

            # Install Docker and AWS CLI
            add_log("📦 Installing Docker and AWS CLI...")
            install_result = await self._run_blocking_command(
                sandbox,
                """
                sudo apt-get update -qq && \
                sudo apt-get install -y -qq docker.io awscli git && \
                sudo systemctl start docker && \
                sudo usermod -aG docker $USER
                """,
                session_id,
                prefix="   ",
                timeout=180
            )
            
            if install_result.exit_code != 0:
                sandbox.kill()
                return {"success": False, "error": "Failed to install Docker"}
            
            add_log("✅ Docker and AWS CLI installed")

            # Clone repository
            add_log(f"📥 Cloning repository...")
            clone_result = await self._run_blocking_command(
                sandbox,
                f"git clone {repository_url} /home/user/repo",
                session_id,
                prefix="   ",
                timeout=300
            )
            
            if clone_result.exit_code != 0:
                sandbox.kill()
                return {"success": False, "error": "Failed to clone repository"}
            
            add_log("✅ Repository cloned")

            # Check if Dockerfile exists and if it's Alpine-based (problematic for Next.js)
            check_result = sandbox.commands.run("test -f /home/user/repo/Dockerfile")
            
            dockerfile_needs_fix = False
            if check_result.exit_code == 0:
                # Dockerfile exists - check if it's Alpine-based
                add_log("📋 Found existing Dockerfile, checking compatibility...")
                
                try:
                    alpine_check = sandbox.commands.run("grep -i 'FROM.*alpine' /home/user/repo/Dockerfile")
                    is_alpine = alpine_check.exit_code == 0
                except Exception:
                    # grep returns exit code 1 if not found - not an error
                    is_alpine = False
                
                if is_alpine:
                    add_log("⚠️ Detected Alpine-based Dockerfile - may have compatibility issues with Next.js 15 + Tailwind v4")
                    
                    # Check if this is a Next.js project (handle grep failure gracefully)
                    try:
                        nextjs_check = sandbox.commands.run("test -f /home/user/repo/package.json && grep -q 'next' /home/user/repo/package.json")
                        is_nextjs = nextjs_check.exit_code == 0
                    except Exception:
                        # grep returns exit code 1 if not found - not an error
                        is_nextjs = False
                    
                    if is_nextjs:
                        add_log("🔧 Auto-fixing: Replacing Alpine Dockerfile with Debian-slim version for better Next.js compatibility...")
                        dockerfile_needs_fix = True
            else:
                add_log("⚠️ No Dockerfile found in repository")
                dockerfile_needs_fix = True
            
            if dockerfile_needs_fix:
                add_log("📝 Creating optimized Next.js Dockerfile...")
                # Create a production-ready Next.js Dockerfile with proper dependencies
                default_dockerfile = """# Multi-stage build for Next.js applications
# Build stage - use debian-based image for better compatibility with native modules
FROM node:20-slim AS builder

WORKDIR /app

# Install dependencies needed for native modules (sharp, lightningcss, etc.)
RUN apt-get update && apt-get install -y \\
    python3 make g++ \\
    && rm -rf /var/lib/apt/lists/*

# Copy package files
COPY package*.json ./

# Install ALL dependencies (including devDependencies for build)
RUN npm ci

# Copy source code
COPY . .

# Build Next.js app
RUN npm run build

# Production stage - minimal runtime image
FROM node:20-slim AS runner

WORKDIR /app

ENV NODE_ENV=production

# Create non-root user
RUN addgroup --system --gid 1001 nodejs && \\
    adduser --system --uid 1001 nextjs

# Copy necessary files from builder
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME=\"0.0.0.0\"

CMD [\"node\", \"server.js\"]
"""
                sandbox.files.write("/home/user/repo/Dockerfile", default_dockerfile)
                add_log("✅ Created production-ready Dockerfile (node:20-slim with native module support)")
                
            # Check for next.config and ensure standalone output
            add_log("🔍 Checking Next.js configuration...")
            try:
                config_check = sandbox.commands.run("test -f /home/user/repo/next.config.* && (grep -q 'output.*standalone' /home/user/repo/next.config.* || echo 'missing')")
                if "missing" in config_check.stdout:
                    add_log("⚠️ Warning: next.config should have 'output: \"standalone\"' for optimal Docker deployment")
                else:
                    add_log("✅ Next.js standalone output configured")
            except Exception:
                # next.config might not exist - not an error
                pass

            # Configure AWS CLI
            add_log("🔑 Configuring AWS credentials...")
            aws_config = f"""#!/bin/bash
export AWS_ACCESS_KEY_ID="{credentials['AccessKeyId']}"
export AWS_SECRET_ACCESS_KEY="{credentials['SecretAccessKey']}"
export AWS_SESSION_TOKEN="{credentials['SessionToken']}"
export AWS_DEFAULT_REGION="{settings.aws_region}"
"""
            sandbox.files.write("/tmp/aws_creds.sh", aws_config)

            # Login to ECR
            add_log("🔐 Logging into ECR...")
            ecr_login_result = await self._run_blocking_command(
                sandbox,
                "source /tmp/aws_creds.sh && aws ecr get-login-password | sudo docker login --username AWS --password-stdin " + ecr_repository_url.split("/")[0],
                session_id,
                prefix="   ",
                timeout=60
            )
            
            if ecr_login_result.exit_code != 0:
                sandbox.kill()
                return {"success": False, "error": "Failed to login to ECR"}
            
            add_log("✅ Logged into ECR")

            # Build Docker image
            image_tag = f"{ecr_repository_url}:latest"
            add_log(f"🔨 Building Docker image...")
            add_log(f"   Image tag: {image_tag}")
            
            build_result = await self._run_blocking_command(
                sandbox,
                f"cd /home/user/repo && sudo docker build -t {image_tag} .",
                session_id,
                prefix="   ",
                timeout=3000  # 50 minutes (leave time for push within 1-hour sandbox limit)
            )
            
            if build_result.exit_code != 0:
                sandbox.kill()
                return {"success": False, "error": "Docker build failed"}
            
            add_log("✅ Docker image built successfully")

            # Push to ECR
            add_log(f"⬆️ Pushing image to ECR...")
            add_log(f"   ⏱️  Large images may take 10-15 minutes to push")
            push_result = await self._run_blocking_command(
                sandbox,
                f"source /tmp/aws_creds.sh && sudo docker push {image_tag}",
                session_id,
                prefix="   ",
                timeout=600  # 10 minutes for push (within 1-hour sandbox limit)
            )
            
            sandbox.kill()
            
            if push_result.exit_code != 0:
                return {"success": False, "error": "Failed to push image to ECR"}
            
            add_log("✅ Image pushed to ECR successfully")
            add_log(f"🎉 Docker image ready: {image_tag}")

            return {
                "success": True,
                "image_tag": image_tag,
                "ecr_repository": ecr_repository_url,
            }

        except Exception as e:
            error_msg = f"Docker build failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            add_log(f"❌ ERROR: {error_msg}")
            return {"success": False, "error": error_msg}


def get_docker_build_service() -> DockerBuildService:
    """Get Docker build service instance."""
    return DockerBuildService()
