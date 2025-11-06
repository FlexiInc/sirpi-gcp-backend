"""
Google ADK Orchestrator for Sirpi DevOps Automation.
Coordinates multi-agent workflow from repository analysis to deployment.
"""

import asyncio
import json
import logging
from typing import Dict, Any, Literal, Optional
from pydantic import BaseModel
from datetime import datetime

# Google ADK imports
from google.adk.agents import Agent, SequentialAgent, ParallelAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService, DatabaseSessionService
from google.adk.tools import ToolContext
from google.genai import types

from .agents.code_analyzer_agent import CodeAnalyzerAgent, AnalysisResult
from .agents.dockerfile_generator_agent import DockerfileGeneratorAgent
from .agents.terraform_generator_agent import TerraformGeneratorAgent
from src.core.config import settings
from src.models.schemas import WorkflowStatus


CloudProvider = Literal["aws", "gcp"]


class WorkflowState(BaseModel):
    """State tracking for the deployment workflow."""

    github_repo_url: str
    local_repo_path: str
    cloud_provider: CloudProvider = "gcp"
    status: str = "PENDING"
    error: Optional[str] = None
    
    # Results
    analysis_result: Optional[AnalysisResult] = None
    generated_dockerfile: Optional[str] = None
    generated_terraform: Optional[Dict[str, str]] = None


class SirpiOrchestrator:
    """
    Main orchestrator coordinating all Sirpi agents using Google ADK.
    Uses ADK's session state for agent-to-agent communication.
    """
    
    def __init__(self, cloud_provider: CloudProvider = "gcp"):
        """
        Initialize orchestrator.
        
        Args:
            cloud_provider: Target cloud for user deployments
        """
        self.logger = logging.getLogger(__name__)
        self.cloud_provider = cloud_provider
        
        # Initialize session service
        self.session_service = self._create_session_service()
        
        # Initialize specialist agents
        self.code_analyzer = CodeAnalyzerAgent()
        self.dockerfile_generator = DockerfileGeneratorAgent()
        self.terraform_generator = TerraformGeneratorAgent(cloud_provider)
        
        self.logger.info(f"Orchestrator initialized for {cloud_provider.upper()} deployments")
    
    def _create_session_service(self):
        """Create ADK session service based on configuration."""
        if settings.adk_session_service_type == "database":
            self.logger.info("Using DatabaseSessionService with Supabase")
            return DatabaseSessionService(db_url=settings.adk_session_db_url)
        else:
            self.logger.info("Using InMemorySessionService")
            return InMemorySessionService()
    
    async def _log_to_session(
        self, user_id: str, session_id: str | None, agent: str, stage: str, content: str
    ) -> None:
        """
        Log agent thinking/activity to database for persistence and real-time streaming.
        Uses PostgreSQL for immediate SSE push (no polling!).
        
        Args:
            user_id: User identifier
            session_id: Session identifier
            agent: Agent name (e.g., 'orchestrator', 'code_analyzer')
            stage: Stage name (e.g., 'analyzing', 'generating')
            content: Log content
        """
        if not session_id:
            return
        
        from datetime import datetime
        from src.services.supabase import supabase
        
        try:
            # Write directly to database (triggers PostgreSQL NOTIFY for SSE)
            with supabase.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_logs (session_id, agent, stage, content, timestamp)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (session_id, agent, stage, content, datetime.utcnow()),
                    )
                conn.commit()
            
            self.logger.debug(f"Logged to database: {agent}.{stage}")
            
        except Exception as e:
            # Log at WARNING level so we can see what's wrong
            self.logger.warning(f"Failed to log to database: {e}", exc_info=True)
    
    async def run_workflow(
        self,
        github_repo_url: str,
        installation_id: int,
        user_id: str,
        session_id: Optional[str] = None,
        sse_session: Optional[Dict[str, Any]] = None,
        log_func: Optional[callable] = None,  # Optional logging function from execute()
    ) -> WorkflowState:
        """
        Execute the full deployment workflow.
        
        Args:
            github_repo_url: GitHub repository URL
            installation_id: GitHub App installation ID
            user_id: User identifier
            session_id: Optional session ID (for tracking)
            
        Returns:
            WorkflowState with all results
        """
        # Debug: Check if sse_session was passed
        self.logger.info(
            f"[SSE DEBUG] run_workflow started. sse_session is: {'SET' if sse_session else 'NONE'}, ID: {id(sse_session) if sse_session else 'N/A'}"
        )

        state = WorkflowState(
            github_repo_url=github_repo_url,
            local_repo_path="",  # No longer needed
            cloud_provider=self.cloud_provider,
        )
        
        self.logger.info(f"Starting workflow for {github_repo_url} (session: {session_id})")
        
        try:
            # STAGE 1: Fetch Repository Data via GitHub API
            state.status = "ANALYZING"
            await self._log_to_session(
                user_id,
                session_id,
                "orchestrator",
                "starting",
                f"Starting workflow for {github_repo_url}",
            )
            
            self.logger.info("[STAGE 1/3] Fetching repository data via GitHub API...")
            await self._log_to_session(
                user_id,
                session_id,
                "orchestrator",
                "fetching_repo",
                "Fetching repository structure and files via GitHub API",
            )
            
            from src.agentcore.tools.github_analyzer import GitHubAnalyzer, parse_github_url
            from src.services.github_app import get_github_app
            
            github_service = get_github_app()
            github_analyzer = GitHubAnalyzer(github_service=github_service)
            owner, repo = parse_github_url(github_repo_url)
            
            # Fetch raw repository data via API (no cloning!)
            # Pass a progress callback to stream every file fetch
            def progress_callback(message: str):
                if log_func:
                    log_func("GitHub Analyzer", message, "INFO")

            raw_data = await github_analyzer.analyze_repository(
                installation_id, owner, repo, progress_callback=progress_callback
            )
            self.logger.info(
                f"Fetched repository data: {len(raw_data.files)} files, {len(raw_data.package_files)} package files"
            )

            # Log to SSE stream using the log function if available
            if log_func:
                log_func(
                    "GitHub Analyzer",
                    f"Analysis complete: {len(raw_data.files)} total files scanned",
                    "INFO",
                )

            await self._log_to_session(
                user_id,
                session_id,
                "github_analyzer",
                "analyzed",
                f"Repository structure: {len(raw_data.files)} files analyzed, detected {raw_data.detected_language or 'unknown'} language",
            )
            
            # Run AI analysis on the raw data
            self.logger.info("Running AI analysis...")
            await self._log_to_session(
                user_id,
                session_id,
                "code_analyzer",
                "analyzing",
                f"Analyzing {raw_data.detected_language or 'repository'} application structure with Gemini AI",
            )
            
            analysis_result = await self.code_analyzer.analyze(raw_data)
            state.analysis_result = analysis_result
            
            self.logger.info(
                f"Analysis complete: {analysis_result.framework} ({analysis_result.language})"
            )
            
            # Log detailed analysis results
            framework_info = (
                f"{analysis_result.framework} {analysis_result.language}"
                if analysis_result.framework
                else analysis_result.language
            )
            dep_count = len(analysis_result.dependencies)
            env_count = len(analysis_result.environment_variables)
            
            # Log to SSE stream using the log function if available
            if log_func:
                log_func(
                    "Code Analyzer",
                    f"AI analysis complete: Detected {framework_info} on port {analysis_result.exposed_port}",
                    "INFO",
                )
                log_func(
                    "Code Analyzer",
                    f"Found {dep_count} dependencies, {env_count} environment variables",
                    "INFO",
                )

            await self._log_to_session(
                user_id,
                session_id,
                "code_analyzer",
                "completed",
                f"Detected {framework_info} on port {analysis_result.exposed_port} with {dep_count} dependencies and {env_count} environment variables",
            )
            
            # STAGE 2: Parallel Artifact Generation
            state.status = "GENERATING"
            self.logger.info("[STAGE 2/3] Generating deployment artifacts...")
            await self._log_to_session(
                user_id,
                session_id,
                "orchestrator",
                "generating",
                "Generating Dockerfile and Terraform configurations",
            )
            
            # Log Dockerfile generation start
            runtime_info = analysis_result.runtime_version or analysis_result.language
            await self._log_to_session(
                user_id,
                session_id,
                "dockerfile_generator",
                "generating",
                f"Generating production-hardened Dockerfile with {runtime_info} runtime and multi-stage build",
            )
            
            # Run Dockerfile and Terraform generation in parallel
            dockerfile_task = self.dockerfile_generator.generate(analysis_result)
            terraform_task = asyncio.to_thread(
                self.terraform_generator.generate, github_repo_url, analysis_result
            )
            
            dockerfile, terraform_files = await asyncio.gather(dockerfile_task, terraform_task)
            
            state.generated_dockerfile = dockerfile
            state.generated_terraform = terraform_files
            
            self.logger.info("Dockerfile and Terraform generated")
            
            # Log generation completion with details
            dockerfile_size = len(dockerfile)
            await self._log_to_session(
                user_id,
                session_id,
                "dockerfile_generator",
                "completed",
                f"Multi-stage Dockerfile generated ({dockerfile_size} bytes) with security hardening and health checks",
            )
            
            await self._log_to_session(
                user_id,
                session_id,
                "terraform_generator",
                "completed",
                f"Infrastructure configuration ready: {', '.join(terraform_files.keys())} for {self.cloud_provider.upper()} deployment",
            )
            
            # STAGE 3: Finalization
            state.status = "SUCCESS"
            self.logger.info("[STAGE 3/3] Workflow complete!")
            await self._log_to_session(
                user_id,
                session_id,
                "orchestrator",
                "completed",
                "Workflow completed successfully. All files generated and ready for deployment.",
            )
            
            return state
            
        except Exception as e:
            self.logger.error(f"Workflow failed at {state.status}: {e}", exc_info=True)
            state.status = "FAILED"
            state.error = str(e)
            
            # Log failure to session
            await self._log_to_session(
                user_id, session_id, "orchestrator", "failed", f"Workflow failed: {str(e)}"
            )
            
            return state
    
    def _extract_service_name(self, repo_url: str) -> str:
        """Extract clean service name from GitHub URL."""
        import re

        try:
            repo_name = repo_url.split("/")[-1].replace(".git", "")
            # Sanitize for Cloud Run (lowercase, hyphens only)
            return re.sub(r"[^a-z0-9-]", "-", repo_name.lower()).strip("-")
        except Exception:
            return "sirpi-service"
    
    async def get_session_state(self, user_id: str, session_id: str) -> Dict[str, Any]:
        """
        Retrieve session state.
        
        Args:
            user_id: User identifier
            session_id: Session identifier
            
        Returns:
            Session state dictionary
        """
        try:
            session = await self.session_service.get_session(
                app_name=settings.adk_app_name, user_id=user_id, session_id=session_id
            )
            return session.state
        except Exception as e:
            self.logger.error(f"Failed to get session state: {e}")
            return {}
    
    async def stream_agent_logs(self, session_id: str):
        """
        Stream agent logs using PostgreSQL LISTEN/NOTIFY.
        Properly handles cancellation to avoid hanging on shutdown.
        
        Args:
            session_id: Session identifier
            
        Yields:
            Dict with event type and log data
        """
        from src.services.supabase import supabase
        import select
        
        # First, yield any existing logs from database
        try:
            with supabase.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT agent, stage, content, timestamp
                        FROM agent_logs
                        WHERE session_id = %s
                        ORDER BY timestamp ASC
                        """,
                        (session_id,),
                    )
                    
                    rows = cur.fetchall()
                    for row in rows:
                        yield {
                            "event": "agent_log",
                            "data": json.dumps(
                                {
                                "agent": row["agent"],
                                "stage": row["stage"],
                                "content": row["content"],
                                    "timestamp": row["timestamp"].isoformat(),
                                }
                            ),
                        }
                        
        except Exception as e:
            # Log but continue - historical logs are optional
            self.logger.debug(f"No historical logs: {e}")
        
        # Now listen for new logs via PostgreSQL NOTIFY
        conn = None
        cur = None
        
        try:
            conn = supabase.get_raw_connection()
            conn.set_isolation_level(0)  # AUTOCOMMIT for LISTEN
            cur = conn.cursor()
            
            # Subscribe to notifications
            channel = f"agent_logs_{session_id}"
            cur.execute(f"LISTEN {channel};")
            self.logger.info(f"Listening on channel: {channel}")
            
            # Event-driven wait (blocks until notification or timeout)
            max_wait_seconds = 120  # 2 minute max
            start_time = asyncio.get_event_loop().time()
            
            while True:
                # Check if we should stop (timeout or workflow complete)
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > max_wait_seconds:
                    self.logger.info("Max wait time reached, closing stream")
                    break
                
                # Use short timeout for select to allow cancellation
                if select.select([conn], [], [], 1.0) != ([], [], []):
                    conn.poll()
                    while conn.notifies:
                        notify = conn.notifies.pop(0)
                        log_data = json.loads(notify.payload)
                        
                        yield {"event": "agent_log", "data": json.dumps(log_data)}
                        
                        # Stop on completion/failure
                        if log_data.get("stage") in ["completed", "failed"]:
                            if log_data.get("agent") == "orchestrator":
                                self.logger.info(
                                    f"Workflow {log_data.get('stage')}, closing stream"
                                )
                                return
                
                # Allow other tasks to run
                await asyncio.sleep(0)
                
        except asyncio.CancelledError:
            self.logger.info("Agent log stream cancelled")
            raise
        except GeneratorExit:
            self.logger.info("Agent log stream closed by client")
            raise
        except Exception as e:
            self.logger.error(f"Agent log stream error: {e}", exc_info=True)
            yield {"event": "error", "data": json.dumps({"error": str(e)})}
        finally:
            # Always cleanup PostgreSQL LISTEN connection
            if cur:
                try:
                    cur.execute(f"UNLISTEN {channel};")
                except Exception:
                    pass
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            self.logger.debug("Agent log stream cleanup complete")
    
    async def execute(
        self,
        session_id: str,
        repository_url: str,
        installation_id: int,
        template_type: str,
        project_id: Optional[str] = None,
        session: Optional[Dict[str, Any]] = None,
    ):
        """
        Execute the full workflow with all integrations.
        Organizes logs by stage and persists to database.
        
        Args:
            session_id: Unique session identifier
            repository_url: GitHub repository URL
            installation_id: GitHub App installation ID
            template_type: Cloud Run, GKE, etc.
            project_id: Optional project UUID
            session: Session dict for logging (SSE streaming)
        """
        from src.agentcore.tools.github_analyzer import parse_github_url
        from src.services.gcs_storage import get_gcs_storage
        from src.services.supabase import supabase
        import asyncio
        import time
        
        # Track logs by stage
        stage_logs: Dict[str, list] = {
            "analyze": [],
            "ai_analysis": [],
            "generate": [],
            "upload": [],
        }
        stage_timers: Dict[str, float] = {}
        current_stage = None
        
        def log(agent: str, message: str, level: str = "INFO", stage: Optional[str] = None):
            """Log to session for SSE streaming and organize by stage."""
            timestamp_str = datetime.now().strftime("%I:%M:%S %p")
            formatted_log = f"{timestamp_str}  [{agent.upper()}] {message}"
            
            if session:
                log_entry = {
                    "timestamp": datetime.now(),
                    "agent": agent,
                    "message": message,
                    "level": level,
                }
                session["logs"].append(log_entry)
            
            # Add to stage-specific logs with formatting
            if stage and stage in stage_logs:
                stage_logs[stage].append(formatted_log)
            
            self.logger.log(getattr(logging, level), f"[{agent}] {message}")
        
        def save_stage(stage: str, status: str):
            """Save completed stage logs to database."""
            try:
                duration = None
                if stage in stage_timers:
                    duration = int(time.time() - stage_timers[stage])
                
                supabase.save_workflow_stage_logs(
                    session_id=session_id,
                    stage=stage,
                    logs=stage_logs[stage],
                    status=status,
                    duration_seconds=duration,
                )
            except Exception as e:
                self.logger.warning(f"Failed to save stage logs: {e}")
        
        try:
            # Update session status
            if session:
                session["status"] = WorkflowStatus.ANALYZING
            
            # Stage 1: Repository Analysis
            stage_timers["analyze"] = time.time()
            log("Orchestrator", f"Starting workflow for {repository_url}", stage="analyze")
            
            # Extract repo info
            owner, repo = parse_github_url(repository_url)
            log("Orchestrator", f"Repository: {owner}/{repo}", stage="analyze")
            
            # Run the core ADK workflow (uses GitHub API, no cloning!)
            log("Orchestrator", "Fetching repository structure via GitHub API...", stage="analyze")
            if session:
                session["status"] = WorkflowStatus.ANALYZING
            
            # Stage 2: AI Analysis  
            stage_timers["ai_analysis"] = time.time()
            log("Code Analyzer", "Starting AI-powered code analysis", stage="ai_analysis")
            
            # Debug: Verify session reference
            if session:
                self.logger.info(
                    f"[SSE DEBUG] Session before run_workflow: {id(session)}, logs count: {len(session.get('logs', []))}"
                )

            workflow_state = await self.run_workflow(
                github_repo_url=repository_url,
                installation_id=installation_id,
                user_id=session.get("user_id", "unknown") if session else "unknown",
                session_id=session_id,
                sse_session=session,  # Pass session for SSE logging
                log_func=log,  # Pass the log function so run_workflow can log to SSE
            )

            # Debug: Verify logs were added
            if session:
                self.logger.info(
                    f"[SSE DEBUG] Session after run_workflow: {id(session)}, logs count: {len(session.get('logs', []))}"
            )
            
            if workflow_state.status == "FAILED":
                log(
                    "Orchestrator",
                    f"Workflow failed: {workflow_state.error}",
                    "ERROR",
                    stage="ai_analysis",
                )
                # Save failed stages
                save_stage("analyze", "error")
                save_stage("ai_analysis", "error")
                raise Exception(workflow_state.error or "Workflow failed")
            
            # Log analysis completion to ai_analysis stage
            if workflow_state.analysis_result:
                framework = (
                    workflow_state.analysis_result.framework
                    or workflow_state.analysis_result.language
                )
                log(
                    "Code Analyzer",
                    f"Detected {framework} on port {workflow_state.analysis_result.exposed_port}",
                    stage="ai_analysis",
                )
                log(
                    "Code Analyzer",
                    f"Found {len(workflow_state.analysis_result.dependencies)} dependencies: {', '.join(list(workflow_state.analysis_result.dependencies.keys())[:3])}...",
                    stage="ai_analysis",
                )
                log(
                    "Code Analyzer",
                    f"Environment variables required: {', '.join(workflow_state.analysis_result.environment_variables[:5])}",
                    stage="ai_analysis",
                )
            
            # Mark analysis stages complete
            save_stage("analyze", "success")
            save_stage("ai_analysis", "success")
            
            # Stage 3: File Generation
            stage_timers["generate"] = time.time()
            log("Generator", "Starting infrastructure file generation", stage="generate")
            log(
                "Orchestrator", "Infrastructure generation completed successfully", stage="generate"
            )
            if workflow_state.analysis_result:
                framework = (
                    workflow_state.analysis_result.framework
                    or workflow_state.analysis_result.language
                )
                log(
                    "Dockerfile Generator",
                    f"Generated production Dockerfile for {framework}",
                    stage="generate",
                )
                log(
                    "Terraform Generator",
                    f"Generated {len(workflow_state.generated_terraform or {})} Terraform files: {', '.join((workflow_state.generated_terraform or {}).keys())}",
                    stage="generate",
                )
                log(
                    "Generator",
                    f"Multi-stage build with {workflow_state.analysis_result.runtime_version or framework} runtime",
                    stage="generate",
                )
            save_stage("generate", "success")
            if session:
                session["status"] = WorkflowStatus.GENERATING
            
            # Prepare files for upload
            files_to_upload = []
            
            if workflow_state.generated_dockerfile:
                files_to_upload.append(
                    {
                    "path": "Dockerfile",
                    "content": workflow_state.generated_dockerfile,
                    "description": "Generated Dockerfile",
                    }
                )
            
            if workflow_state.generated_terraform:
                for filename, content in workflow_state.generated_terraform.items():
                    files_to_upload.append(
                        {
                        "path": filename,
                        "content": content,
                        "description": f"Generated {filename}",
                        }
                    )
            
            # Upload files to GCS
            stage_timers["upload"] = time.time()
            log(
                "Storage",
                f"Preparing to upload {len(files_to_upload)} files to GCS",
                stage="upload",
            )
            gcs_storage = get_gcs_storage()
            
            # Delete old files first to avoid mixing AWS and GCP templates
            deleted_count = gcs_storage.delete_repository_files(owner, repo)
            if deleted_count > 0:
                log("Storage", f"Cleared {deleted_count} old files from storage", stage="upload")
            
            gcs_keys = []
            
            for file_data in files_to_upload:
                # Create GCS path: {owner}/{repo}/{filepath}
                gcs_path = f"{owner}/{repo}/{file_data['path']}"
                gcs_key = gcs_storage.upload_file(
                    content=file_data["content"], file_path=gcs_path, content_type="text/plain"
                )
                gcs_keys.append(gcs_key)
                file_size_kb = len(file_data["content"]) // 1024
                log("Storage", f"Uploaded {file_data['path']} ({file_size_kb}KB)", stage="upload")
            
            log(
                "Storage",
                f"Successfully uploaded all {len(files_to_upload)} files to cloud storage",
                stage="upload",
            )
            save_stage("upload", "success")
            
            # Update session with files
            if session:
                session["files"] = files_to_upload
                session["status"] = WorkflowStatus.COMPLETED
            
            # Update database
            log("Database", "Updating generation record...")
            supabase.update_generation_status(
                session_id=session_id,
                status=WorkflowStatus.COMPLETED.value,
                files=files_to_upload,
                s3_keys=gcs_keys,
                project_context=workflow_state.analysis_result.model_dump()
                if workflow_state.analysis_result
                else None,
            )
            
            # Update project status if project_id provided
            if project_id:
                try:
                    # Determine cloud provider from template_type
                    cloud_provider = "gcp"  # default
                    if template_type:
                        template_str = str(template_type).lower()
                        if (
                            "fargate" in template_str
                            or "ecs" in template_str
                            or "lambda" in template_str
                        ):
                            cloud_provider = "aws"
                        elif "cloud-run" in template_str or "gke" in template_str:
                            cloud_provider = "gcp"

                    supabase.update_project_generation_status(
                        project_id=project_id,
                        status="completed",
                        increment_count=True,
                        cloud_provider=cloud_provider,
                    )
                except Exception as e:
                    log("Database", f"Failed to update project status: {e}", "WARNING")
            
            log("Orchestrator", "Workflow completed successfully!")
            
            # Small delay to ensure SSE can send final logs
            await asyncio.sleep(0.5)
            
        except Exception as e:
            self.logger.error(f"Workflow failed: {e}", exc_info=True)
            
            # Determine which stage failed based on logs
            failed_stage = None
            if not stage_logs["analyze"] or len(stage_logs["analyze"]) < 2:
                failed_stage = "analyze"
            elif not stage_logs["ai_analysis"] or len(stage_logs["ai_analysis"]) < 2:
                failed_stage = "ai_analysis"
            elif not stage_logs["generate"] or len(stage_logs["generate"]) < 2:
                failed_stage = "generate"
            elif not stage_logs["upload"] or len(stage_logs["upload"]) < 2:
                failed_stage = "upload"
            
            if failed_stage:
                log("Orchestrator", f"Workflow failed: {str(e)}", "ERROR", stage=failed_stage)
                save_stage(failed_stage, "error")
            else:
                log("Orchestrator", f"Workflow failed: {str(e)}", "ERROR")
            
            # Mark all incomplete stages as error
            for stage_name in ["analyze", "ai_analysis", "generate", "upload"]:
                if len(stage_logs.get(stage_name, [])) == 0:
                    # Stage never started - mark as idle, not error
                    pass
                elif stage_name in stage_timers:
                    # Stage started but not saved - save with error
                    if stage_name not in [s for s in stage_logs if stage_logs[s]]:
                        save_stage(stage_name, "error")
            
            if session:
                session["status"] = WorkflowStatus.FAILED
                session["error"] = str(e)
            
            # Update database with failure
            try:
                supabase.update_generation_status(
                    session_id=session_id,
                    status=WorkflowStatus.FAILED.value,
                    error=str(e),
                )
                
                if project_id:
                    supabase.update_project_generation_status(
                        project_id=project_id,
                        status="failed",
                        increment_count=False,
                    )
            except Exception:
                pass


# Backward compatibility alias
WorkflowOrchestrator = SirpiOrchestrator
