"""
Deployment Service - Executes terraform with E2B streaming and cross-account role assumption.
"""

import boto3
import json
import logging
import uuid
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

from src.core.config import settings

# E2B imports for streaming deployment
try:
    from e2b_code_interpreter import Sandbox
    E2B_AVAILABLE = True
except ImportError:
    E2B_AVAILABLE = False
    logging.warning("E2B not available, deployment functionality will be limited")

logger = logging.getLogger(__name__)


@dataclass
class DeploymentResult:
    """Result of deployment execution."""
    success: bool
    logs: List[str]
    error: Optional[str] = None
    outputs: Optional[Dict] = None


class DeploymentError(Exception):
    """Deployment operation error."""
    pass


class DeploymentService:
    """
    Service for executing terraform deployments with E2B streaming and cross-account support.
    """

    def __init__(self):
        self.terraform_version = "1.5.0"
        self.executor = ThreadPoolExecutor(max_workers=4)

    def _add_log_to_session(self, session_id: str, message: str):
        """Add log message to active deployment session for SSE streaming."""
        try:
            from src.api.deployments import active_deployment_sessions
            
            if session_id in active_deployment_sessions:
                active_deployment_sessions[session_id]["logs"].append(message)
        except Exception as e:
            logger.error(f"Failed to add log to session: {e}")

    async def _run_blocking_command(self, sandbox, command: str, session_id: str, prefix: str = "", timeout: int = 300):
        """Run a blocking sandbox command in thread pool with periodic yields for real-time streaming."""
        loop = asyncio.get_event_loop()
        
        def run_command():
            return sandbox.commands.run(
                command,
                on_stdout=lambda line: self._add_log_to_session(session_id, f"{prefix}{line.strip()}") if line.strip() else None,
                on_stderr=lambda line: self._add_log_to_session(session_id, f"{prefix}⚠️ {line.strip()}") if line.strip() else None,
                timeout=timeout
            )
        
        # Run in thread pool to avoid blocking event loop
        result = await loop.run_in_executor(self.executor, run_command)
        return result

    async def _install_terraform_in_sandbox(self, sandbox, session_id: str) -> bool:
        """Install Terraform in E2B sandbox if not already installed."""
        logs = []
        
        def add_log(message: str):
            logs.append(message)
            self._add_log_to_session(session_id, message)
            logger.info(f"[Install] {message}")
        
        try:
            add_log("📦 Installing Terraform in sandbox...")
            
            # Check if terraform is already installed
            try:
                check_result = sandbox.commands.run("terraform version")
                if check_result.exit_code == 0:
                    add_log("✅ Terraform already installed")
                    add_log(f"   {check_result.stdout.strip()}")
                    return True
            except Exception:
                # Terraform not installed, continue with installation
                pass
            
            # Install Terraform
            install_commands = """
            sudo apt-get update -qq && \
            sudo apt-get install -y -qq wget unzip && \
            wget -q https://releases.hashicorp.com/terraform/1.6.0/terraform_1.6.0_linux_amd64.zip && \
            unzip -q terraform_1.6.0_linux_amd64.zip && \
            sudo mv terraform /usr/local/bin/ && \
            rm terraform_1.6.0_linux_amd64.zip
            """
            
            add_log("   Downloading Terraform...")
            
            result = await self._run_blocking_command(
                sandbox,
                install_commands,
                session_id,
                prefix="   ",
                timeout=180
            )
            
            if result.exit_code == 0:
                add_log("✅ Terraform installed successfully")
                
                # Verify installation
                version_result = sandbox.commands.run("terraform version")
                if version_result.exit_code == 0:
                    version_output = version_result.stdout.strip()
                    add_log(f"   {version_output}")
                    return True
                else:
                    add_log("❌ Failed to verify Terraform installation")
                    return False
            else:
                add_log(f"❌ Terraform installation failed with exit code {result.exit_code}")
                return False
                
        except Exception as e:
            add_log(f"❌ Error installing Terraform: {str(e)}")
            logger.error(f"Terraform installation error: {e}", exc_info=True)
            return False

    async def deploy_infrastructure(
        self,
        session_id: str,
        project_id: str,
        log_stream: Optional[object] = None,
        role_arn: str = None,
        external_id: str = None,
    ) -> DeploymentResult:
        """
        Deploy infrastructure using E2B sandbox with real-time streaming.
        """
        logs = []

        def add_log(message: str):
            """Add log to both local list and session for streaming."""
            logs.append(message)
            self._add_log_to_session(session_id, message)
            logger.info(f"[Deploy] {message}")

        try:
            if not E2B_AVAILABLE:
                add_log("❌ E2B not available, cannot run terraform deployment")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="E2B sandbox not available"
                )

            add_log("🚀 Starting infrastructure deployment with E2B sandbox...")

            # 1. Get project generation data from database
            add_log("📋 Getting project generation data from database...")
            from src.services.supabase import supabase

            generation = supabase.get_latest_generation_by_project(project_id)
            if not generation:
                add_log("❌ No generations found for project")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="No generations found for project"
                )

            add_log(f"✅ Found generation: {generation['id']}")

            # 2. Get repository info from generation
            repository_url = generation.get("repository_url", "")
            if not repository_url:
                add_log("❌ No repository URL found in generation")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="No repository URL found"
                )

            # Extract owner/repo from GitHub URL
            try:
                parts = repository_url.replace("https://github.com/", "").split("/")
                if len(parts) >= 2:
                    owner = parts[0]
                    repo = parts[1]
                    add_log(f"📋 Repository: {owner}/{repo}")
                else:
                    add_log("❌ Invalid repository URL format")
                    return DeploymentResult(
                        success=False, 
                        logs=logs, 
                        error="Invalid repository URL format"
                    )
            except Exception as e:
                add_log(f"❌ Failed to parse repository URL: {str(e)}")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error=f"Failed to parse repository URL: {str(e)}"
                )

            # 3. Download Terraform files from GCS
            add_log("📁 Downloading Terraform files from GCS...")
            from src.services.gcs_storage import get_gcs_storage

            gcs_storage = get_gcs_storage()
            files_data = await gcs_storage.get_repository_files(
                owner=owner, repo=repo, include_content=True
            )

            terraform_files = {}
            for file_data in files_data:
                filename = file_data["filename"]
                if filename.endswith(".tf"):
                    terraform_files[filename] = file_data["content"]
                    add_log(f"  📄 Found: {filename}")

            if not terraform_files:
                add_log(f"❌ No Terraform files found in GCS for this repository")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="No Terraform files found in GCS"
                )

            add_log(f"✅ Found {len(terraform_files)} Terraform files")

            # 4. Assume AWS role to get temporary credentials
            add_log("🔐 Assuming AWS role in your account...")
            add_log(f"   Role ARN: {role_arn}")
            add_log(f"   External ID: {external_id}")

            try:
                credentials = self.assume_cross_account_role(role_arn, external_id)
                add_log("✅ Successfully obtained temporary AWS credentials")
            except Exception as e:
                add_log(f"❌ Failed to assume role: {str(e)}")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error=f"Failed to assume AWS role: {str(e)}"
                )

            # 5. Create E2B sandbox with user's AWS credentials
            add_log("🏗️ Creating E2B sandbox...")
            add_log(f"   Using API key: {settings.e2b_api_key[:10]}...")
            add_log(f"   Region: {settings.aws_region}")

            try:
                sandbox = Sandbox.create(api_key=settings.e2b_api_key)
                add_log("✅ E2B sandbox created successfully")
            except Exception as e:
                add_log(f"❌ Failed to create E2B sandbox: {str(e)}")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error=f"Failed to create E2B sandbox: {str(e)}"
                )

            # 6. Install Terraform in sandbox
            terraform_installed = await self._install_terraform_in_sandbox(sandbox, session_id)
            if not terraform_installed:
                add_log("❌ Failed to install Terraform")
                sandbox.kill()
                return DeploymentResult(
                    success=False,
                    logs=logs,
                    error="Failed to install Terraform in sandbox"
                )

            # 7. Set AWS credentials as environment variables
            add_log("🔑 Configuring AWS credentials in sandbox...")
            
            # Create a script to export credentials
            creds_script = f"""#!/bin/bash
export AWS_ACCESS_KEY_ID="{credentials['AccessKeyId']}"
export AWS_SECRET_ACCESS_KEY="{credentials['SecretAccessKey']}"
export AWS_SESSION_TOKEN="{credentials['SessionToken']}"
export AWS_DEFAULT_REGION="{settings.aws_region}"
"""
            sandbox.files.write("/tmp/aws_creds.sh", creds_script)
            add_log("✅ AWS credentials configured")

            # 8. Upload Terraform files to sandbox
            add_log("📁 Uploading Terraform files to E2B sandbox...")
            upload_count = 0
            
            # Create terraform directory
            sandbox.commands.run("mkdir -p /home/user/terraform")
            
            # Get aws_connection to extract account_id for backend.tf
            from src.services.supabase import supabase
            aws_connection = supabase.get_aws_connection_by_id(
                supabase.get_project_by_id(project_id)["aws_connection_id"]
            )
            account_id = aws_connection.get("account_id") if aws_connection else None
            
            for filename, content in terraform_files.items():
                try:
                    # Regenerate backend.tf with correct bucket name (includes account_id)
                    if filename == "backend.tf" and account_id:
                        add_log(f"  🔧 Regenerating backend.tf with account ID: {account_id}")
                        from src.agentcore.templates.terraform_backend import generate_backend_config
                        content = generate_backend_config(
                            project_id=project_id,
                            account_id=account_id
                        )
                        add_log(f"  ✅ Updated backend.tf: sirpi-terraform-states-{account_id}")
                    
                    # Write file to sandbox in terraform directory
                    sandbox.files.write(f"/home/user/terraform/{filename}", content)
                    upload_count += 1
                    add_log(f"  📄 Uploaded: {filename}")
                except Exception as e:
                    add_log(f"  ⚠️ Failed to upload {filename}: {str(e)}")

            add_log(f"✅ Uploaded {upload_count} files")

            # 9. Ensure ECS service-linked role exists (pre-flight check)
            add_log("🔍 Checking for ECS service-linked role...")
            try:
                # Use the assumed role credentials to create service-linked role if needed
                iam_client = boto3.client(
                    "iam",
                    aws_access_key_id=credentials['AccessKeyId'],
                    aws_secret_access_key=credentials['SecretAccessKey'],
                    aws_session_token=credentials['SessionToken'],
                    region_name=settings.aws_region
                )
                
                try:
                    # Try to create the service-linked role
                    iam_client.create_service_linked_role(
                        AWSServiceName='ecs.amazonaws.com',
                        Description='Service-linked role for Amazon ECS'
                    )
                    add_log("✅ Created ECS service-linked role")
                except iam_client.exceptions.InvalidInputException:
                    # Role already exists - this is fine
                    add_log("✅ ECS service-linked role already exists")
                except Exception as slr_error:
                    # Check if error is because role already exists
                    if "already exists" in str(slr_error).lower():
                        add_log("✅ ECS service-linked role already exists")
                    else:
                        add_log(f"⚠️ Warning: Could not verify service-linked role: {slr_error}")
                        add_log("⚠️ Continuing anyway - role may exist...")
            except Exception as e:
                add_log(f"⚠️ Warning: Service-linked role check failed: {e}")
                add_log("⚠️ Continuing anyway...")

            # 10. Run terraform init with streaming
            add_log("🔧 Running terraform init...")
            
            try:
                init_result = await self._run_blocking_command(
                    sandbox,
                    "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform init",
                    session_id,
                    prefix="   ",
                    timeout=300
                )
                
                if init_result.exit_code == 0:
                    add_log("✅ Terraform init completed successfully")
                else:
                    error_msg = f"Terraform init failed with exit code {init_result.exit_code}"
                    add_log(f"❌ {error_msg}")
                    sandbox.kill()
                    return DeploymentResult(
                        success=False, 
                        logs=logs, 
                        error=error_msg
                    )
                    
            except Exception as e:
                add_log(f"❌ Terraform init failed: {str(e)}")
                sandbox.kill()
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error=f"Terraform init failed: {str(e)}"
                )

            # 10. Run terraform apply with streaming
            add_log("🚀 Running terraform apply -auto-approve...")
            
            try:
                apply_result = await self._run_blocking_command(
                    sandbox,
                    "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform apply -auto-approve -no-color -var='enable_https=false'",
                    session_id,
                    prefix="   ",
                    timeout=600
                )
                
                if apply_result.exit_code == 0:
                    add_log("✅ Terraform apply completed successfully")
                    add_log("🎉 Infrastructure deployed!")
                    
                    # Get outputs
                    add_log("📊 Retrieving terraform outputs...")
                    outputs = None
                    try:
                        output_result = sandbox.commands.run(
                            "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform output -json"
                        )
                        
                        if output_result.exit_code == 0 and output_result.stdout:
                            outputs = json.loads(output_result.stdout)
                            add_log(f"✅ Retrieved {len(outputs)} outputs")
                            
                            # FIRST: Save outputs to database (priority!)
                            try:
                                logger.info(f"Saving terraform outputs: {list(outputs.keys())}")
                                clean_outputs = {
                                    key: output.get('value') if isinstance(output, dict) else output
                                    for key, output in outputs.items()
                                }
                                supabase.save_terraform_outputs(
                                    project_id=project_id,
                                    outputs=clean_outputs
                                )
                                
                                # Extract and save application URL directly
                                alb_dns = clean_outputs.get('alb_dns_name')
                                if alb_dns:
                                    supabase.update_application_url(project_id, alb_dns)
                                    logger.info(f"✅ Application URL saved: {alb_dns}")
                                
                                logger.info("✅ Terraform outputs saved to database")
                                add_log("✅ Application URL saved to database")
                            except Exception as output_error:
                                logger.error(f"Failed to save terraform outputs: {output_error}")
                                add_log(f"⚠️  Failed to save outputs: {output_error}")
                            
                            # SECOND: Try to generate deployment summary (optional)
                            add_log("📝 Generating deployment summary...")
                            try:
                                from src.services.deployment_summary import DeploymentSummaryFormatter
                                
                                formatter = DeploymentSummaryFormatter()
                                
                                # Get all apply logs
                                apply_logs = "\n".join(logs)
                                
                                # Parse summary
                                summary = formatter.parse_terraform_output(
                                    apply_logs,
                                    repo_name=repo
                                )
                                
                                # Save summary to database
                                summary_json = formatter.format_summary_json(summary)
                                
                                with supabase.get_connection() as conn:
                                    with conn.cursor() as cur:
                                        from psycopg2.extras import Json
                                        cur.execute(
                                            """
                                            UPDATE projects
                                            SET deployment_summary = %s,
                                                updated_at = NOW()
                                            WHERE id = %s
                                            """,
                                            (Json(summary_json), project_id)
                                        )
                                
                                add_log(f"✅ Deployment summary saved ({summary.total_resources} resources)")
                                
                            except Exception as e:
                                logger.warning(f"Failed to generate deployment summary: {e}")
                                add_log(f"⚠️  Could not generate summary (outputs still saved): {str(e)}")
                    except Exception as e:
                        logger.warning(f"Failed to get outputs: {e}")
                        add_log(f"⚠️  Could not retrieve outputs: {str(e)}")
                    
                    sandbox.kill()
                    return DeploymentResult(success=True, logs=logs)
                    
                else:
                    error_msg = f"Terraform apply failed with exit code {apply_result.exit_code}"
                    add_log(f"❌ {error_msg}")
                    sandbox.kill()
                    return DeploymentResult(
                        success=False, 
                        logs=logs, 
                        error=error_msg
                    )
                    
            except Exception as e:
                add_log(f"❌ Terraform apply failed: {str(e)}")
                sandbox.kill()
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error=f"Terraform apply failed: {str(e)}"
                )

        except Exception as e:
            error_msg = f"Deployment failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            add_log(f"❌ ERROR: {error_msg}")
            return DeploymentResult(success=False, logs=logs, error=error_msg)

    async def plan_infrastructure(
        self,
        session_id: str,
        project_id: str,
        log_stream: Optional[object] = None,
        role_arn: str = None,
        external_id: str = None,
    ) -> DeploymentResult:
        """
        Plan infrastructure deployment with E2B streaming.
        """
        logs = []

        def add_log(message: str):
            logs.append(message)
            self._add_log_to_session(session_id, message)
            logger.info(f"[Plan] {message}")

        try:
            if not E2B_AVAILABLE:
                add_log("❌ E2B not available")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="E2B sandbox not available"
                )

            add_log("📊 Starting infrastructure planning...")

            # Get generation data
            from src.services.supabase import supabase
            generation = supabase.get_latest_generation_by_project(project_id)
            
            if not generation:
                add_log("❌ No generations found")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="No generations found"
                )

            # Extract owner/repo
            repository_url = generation.get("repository_url", "")
            parts = repository_url.replace("https://github.com/", "").split("/")
            owner, repo = parts[0], parts[1]
            add_log(f"📋 Repository: {owner}/{repo}")

            # Download Terraform files
            from src.services.gcs_storage import get_gcs_storage
            gcs_storage = get_gcs_storage()
            files_data = await gcs_storage.get_repository_files(
                owner=owner, repo=repo, include_content=True
            )

            terraform_files = {
                f["filename"]: f["content"] 
                for f in files_data 
                if f["filename"].endswith(".tf")
            }
            
            add_log(f"✅ Found {len(terraform_files)} Terraform files")

            # Assume role
            add_log("🔐 Assuming AWS role...")
            credentials = self.assume_cross_account_role(role_arn, external_id)
            add_log("✅ Got temporary credentials")

            # Create sandbox
            add_log("🏗️ Creating E2B sandbox...")
            sandbox = Sandbox.create(api_key=settings.e2b_api_key)
            add_log("✅ Sandbox created")

            # Install Terraform
            terraform_installed = await self._install_terraform_in_sandbox(sandbox, session_id)
            if not terraform_installed:
                sandbox.kill()
                return DeploymentResult(
                    success=False,
                    logs=logs,
                    error="Failed to install Terraform"
                )

            # Set AWS credentials
            add_log("🔑 Configuring AWS credentials...")
            creds_script = f"""#!/bin/bash
export AWS_ACCESS_KEY_ID="{credentials['AccessKeyId']}"
export AWS_SECRET_ACCESS_KEY="{credentials['SecretAccessKey']}"
export AWS_SESSION_TOKEN="{credentials['SessionToken']}"
export AWS_DEFAULT_REGION="{settings.aws_region}"
"""
            sandbox.files.write("/tmp/aws_creds.sh", creds_script)

            # Upload files
            add_log("📁 Uploading files...")
            sandbox.commands.run("mkdir -p /home/user/terraform")
            
            # Get aws_connection to extract account_id for backend.tf
            from src.services.supabase import supabase
            aws_connection = supabase.get_aws_connection_by_id(
                supabase.get_project_by_id(project_id)["aws_connection_id"]
            )
            account_id = aws_connection.get("account_id") if aws_connection else None
            
            for filename, content in terraform_files.items():
                # Regenerate backend.tf with correct bucket name (includes account_id)
                if filename == "backend.tf" and account_id:
                    add_log(f"  🔧 Regenerating backend.tf with account ID: {account_id}")
                    from src.agentcore.templates.terraform_backend import generate_backend_config
                    content = generate_backend_config(
                        project_id=project_id,
                        account_id=account_id
                    )
                    add_log(f"  ✅ Updated backend.tf: sirpi-terraform-states-{account_id}")
                
                sandbox.files.write(f"/home/user/terraform/{filename}", content)
            add_log(f"✅ Uploaded {len(terraform_files)} files")

            # Terraform init
            add_log("🔧 Running terraform init...")
            init_result = await self._run_blocking_command(
                sandbox,
                "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform init",
                session_id,
                prefix="   ",
                timeout=300
            )
            
            if init_result.exit_code != 0:
                add_log("❌ Init failed")
                sandbox.kill()
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="Terraform init failed"
                )

            # Terraform plan
            add_log("📊 Running terraform plan...")
            plan_result = await self._run_blocking_command(
                sandbox,
                "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform plan -no-color -var='enable_https=false'",
                session_id,
                prefix="   ",
                timeout=300
            )
            
            sandbox.kill()
            
            if plan_result.exit_code == 0:
                add_log("✅ Plan completed successfully")
                return DeploymentResult(success=True, logs=logs)
            else:
                add_log("❌ Plan failed")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="Terraform plan failed"
                )

        except Exception as e:
            error_msg = f"Planning failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            add_log(f"❌ ERROR: {error_msg}")
            return DeploymentResult(success=False, logs=logs, error=error_msg)

    async def destroy_infrastructure(
        self,
        session_id: str,
        project_id: str,
        log_stream: Optional[object] = None,
        role_arn: str = None,
        external_id: str = None,
    ) -> DeploymentResult:
        """
        Destroy infrastructure with E2B streaming.
        """
        logs = []

        def add_log(message: str):
            logs.append(message)
            self._add_log_to_session(session_id, message)
            logger.info(f"[Destroy] {message}")

        try:
            if not E2B_AVAILABLE:
                add_log("❌ E2B not available")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="E2B sandbox not available"
                )

            add_log("🗑️ Starting infrastructure destruction...")

            # Get generation data
            from src.services.supabase import supabase
            generation = supabase.get_latest_generation_by_project(project_id)
            
            if not generation:
                add_log("❌ No generations found")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="No generations found"
                )

            # Extract owner/repo
            repository_url = generation.get("repository_url", "")
            parts = repository_url.replace("https://github.com/", "").split("/")
            owner, repo = parts[0], parts[1]
            add_log(f"📋 Repository: {owner}/{repo}")

            # Download Terraform files
            from src.services.s3_storage import get_s3_storage
            s3_storage = get_s3_storage()
            files_data = await s3_storage.get_repository_files(
                owner=owner, repo=repo, include_content=True
            )

            terraform_files = {
                f["filename"]: f["content"] 
                for f in files_data 
                if f["filename"].endswith(".tf")
            }
            
            add_log(f"✅ Found {len(terraform_files)} Terraform files")

            # Assume role
            add_log("🔐 Assuming AWS role...")
            credentials = self.assume_cross_account_role(role_arn, external_id)
            add_log("✅ Got temporary credentials")

            # Create sandbox
            add_log("🏗️ Creating E2B sandbox...")
            sandbox = Sandbox.create(api_key=settings.e2b_api_key)
            add_log("✅ Sandbox created")

            # Install Terraform
            terraform_installed = await self._install_terraform_in_sandbox(sandbox, session_id)
            if not terraform_installed:
                sandbox.kill()
                return DeploymentResult(
                    success=False,
                    logs=logs,
                    error="Failed to install Terraform"
                )

            # Set AWS credentials
            add_log("🔑 Configuring AWS credentials...")
            creds_script = f"""#!/bin/bash
export AWS_ACCESS_KEY_ID="{credentials['AccessKeyId']}"
export AWS_SECRET_ACCESS_KEY="{credentials['SecretAccessKey']}"
export AWS_SESSION_TOKEN="{credentials['SessionToken']}"
export AWS_DEFAULT_REGION="{settings.aws_region}"
"""
            sandbox.files.write("/tmp/aws_creds.sh", creds_script)

            # Upload files
            add_log("📁 Uploading files...")
            sandbox.commands.run("mkdir -p /home/user/terraform")
            
            # Get aws_connection to extract account_id for backend.tf
            from src.services.supabase import supabase
            aws_connection = supabase.get_aws_connection_by_id(
                supabase.get_project_by_id(project_id)["aws_connection_id"]
            )
            account_id = aws_connection.get("account_id") if aws_connection else None
            
            for filename, content in terraform_files.items():
                # Regenerate backend.tf with correct bucket name (includes account_id)
                if filename == "backend.tf" and account_id:
                    add_log(f"  🔧 Regenerating backend.tf with account ID: {account_id}")
                    from src.agentcore.templates.terraform_backend import generate_backend_config
                    content = generate_backend_config(
                        project_id=project_id,
                        account_id=account_id
                    )
                    add_log(f"  ✅ Updated backend.tf: sirpi-terraform-states-{account_id}")
                
                sandbox.files.write(f"/home/user/terraform/{filename}", content)
            add_log(f"✅ Uploaded {len(terraform_files)} files")

            # Terraform init
            add_log("🔧 Running terraform init...")
            init_result = await self._run_blocking_command(
                sandbox,
                "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform init",
                session_id,
                prefix="   ",
                timeout=300
            )
            
            if init_result.exit_code != 0:
                add_log("❌ Init failed")
                sandbox.kill()
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="Terraform init failed"
                )

            # Terraform destroy
            add_log("🗑️ Running terraform destroy...")
            destroy_result = await self._run_blocking_command(
                sandbox,
                "cd /home/user/terraform && source /tmp/aws_creds.sh && terraform destroy -auto-approve -no-color",
                session_id,
                prefix="   ",
                timeout=600
            )
            
            sandbox.kill()
            
            if destroy_result.exit_code == 0:
                add_log("✅ Infrastructure destroyed successfully")
                
                # Clean up state file from S3 (demo-friendly)
                add_log("🧹 Cleaning up Terraform state file...")
                try:
                    if account_id:
                        state_bucket = f"sirpi-terraform-states-{account_id}"
                        state_key = f"states/{project_id}/terraform.tfstate"
                        
                        s3_client = boto3.client(
                            's3',
                            aws_access_key_id=credentials['AccessKeyId'],
                            aws_secret_access_key=credentials['SecretAccessKey'],
                            aws_session_token=credentials['SessionToken'],
                            region_name=settings.aws_region
                        )
                        
                        # Delete all versions of the state file
                        try:
                            versions = s3_client.list_object_versions(
                                Bucket=state_bucket,
                                Prefix=state_key
                            )
                            
                            # Delete all versions
                            for version in versions.get('Versions', []):
                                s3_client.delete_object(
                                    Bucket=state_bucket,
                                    Key=state_key,
                                    VersionId=version['VersionId']
                                )
                            
                            # Delete all delete markers
                            for marker in versions.get('DeleteMarkers', []):
                                s3_client.delete_object(
                                    Bucket=state_bucket,
                                    Key=state_key,
                                    VersionId=marker['VersionId']
                                )
                            
                            add_log(f"✅ Deleted state file: {state_key}")
                        except Exception as version_error:
                            add_log(f"⚠️  Could not delete state versions: {version_error}")
                    
                except Exception as cleanup_error:
                    logger.warning(f"State cleanup failed: {cleanup_error}")
                    add_log(f"⚠️  State cleanup warning: {cleanup_error}")
                    add_log("⚠️  You may need to empty S3 bucket manually before deleting CloudFormation stack")
                
                # Update project status in database
                add_log("📊 Updating project status...")
                try:
                    supabase.update_project_deployment_status(
                        project_id=project_id,
                        status='destroyed',
                        error=None
                    )
                    
                    # Clear application URL and terraform outputs
                    with supabase.get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE projects
                                SET application_url = NULL,
                                    terraform_outputs = NULL,
                                    deployment_summary = NULL,
                                    deployment_completed_at = NOW(),
                                    updated_at = NOW()
                                WHERE id = %s
                                """,
                                (project_id,)
                            )
                    
                    add_log("✅ Project status updated to 'destroyed'")
                    add_log("✅ Application URL and outputs cleared")
                except Exception as db_error:
                    logger.error(f"Failed to update project status: {db_error}")
                    add_log(f"⚠️  Could not update database: {db_error}")
                
                add_log("🎉 All resources cleaned up successfully!")
                return DeploymentResult(success=True, logs=logs)
            else:
                add_log("❌ Destroy failed")
                return DeploymentResult(
                    success=False, 
                    logs=logs, 
                    error="Terraform destroy failed"
                )

        except Exception as e:
            error_msg = f"Destruction failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            add_log(f"❌ ERROR: {error_msg}")
            return DeploymentResult(success=False, logs=logs, error=error_msg)

    def assume_cross_account_role(self, role_arn: str, external_id: str) -> Dict[str, str]:
        """
        Assume role in user's AWS account using Sirpi's AWS credentials.

        Args:
            role_arn: User's deployment role ARN
            external_id: Unique external ID for security

        Returns:
            Dict with temporary AWS credentials (AccessKeyId, SecretAccessKey, SessionToken)
        """
        logger.info(f"🔐 Assuming role: {role_arn}")
        logger.info(f"🔑 External ID: {external_id}")
        
        try:
            # Lambda uses execution role automatically - no explicit credentials needed
            sts_client = boto3.client(
                "sts",
                region_name=settings.aws_region
            )

            # Assume the user's role
            response = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName=f"sirpi-deploy-{uuid.uuid4().hex[:8]}",
                ExternalId=external_id,
                DurationSeconds=3600,  # 1 hour
            )

            credentials = response["Credentials"]
            logger.info(f"✅ Successfully assumed role")

            # Return credentials in the format expected by E2B
            return {
                "AccessKeyId": credentials["AccessKeyId"],
                "SecretAccessKey": credentials["SecretAccessKey"],
                "SessionToken": credentials["SessionToken"],
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to assume role: {e}", exc_info=True)
            raise DeploymentError(f"Failed to assume deployment role: {str(e)}")


def get_deployment_service() -> DeploymentService:
    """Get deployment service instance."""
    return DeploymentService()
