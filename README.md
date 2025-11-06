# Sirpi Backend

FastAPI backend service for AI-powered cloud deployment automation.

## Structure

```
backend/
├── database/
│   ├── migrations/       # SQL migration files
│   └── schema_complete.sql  # Complete database schema
├── src/
│   ├── agentcore/       # AI orchestration and agents
│   ├── api/             # FastAPI endpoints
│   ├── core/            # Core configuration
│   ├── models/          # Data models and schemas
│   ├── services/        # Business logic services
│   └── utils/           # Helper utilities
├── pyproject.toml       # Python dependencies
└── uv.lock             # Locked dependencies
```

## Key Components

### AI Orchestration (`agentcore/`)
- **Orchestrator** - Manages multi-agent workflow for infrastructure generation
- **Agents** - Specialized agents for analysis, Dockerfile generation, and Terraform creation
- **Templates** - Cloud-specific infrastructure templates (AWS, GCP)

### API Endpoints (`api/`)
- `projects.py` - Project CRUD operations
- `gcp_deployments.py` - GCP deployment lifecycle
- `deployments.py` - AWS deployment lifecycle
- `deployment_logs.py` - SSE log streaming
- `env_vars.py` - Environment variable management
- `gcp_auth.py` - GCP OAuth flow
- `github.py` - GitHub integration
- `health.py` - Health checks

### Services (`services/`)
- **deployment/** - Core deployment logic
  - `sandbox_manager.py` - E2B sandbox orchestration
  - `gcp_deployment.py` - GCP-specific deployment
  - `gcs_state_manager.py` - Terraform state management
- `supabase.py` - Database operations
- `gcs_storage.py` - GCS file storage
- `github_app.py` - GitHub App integration
- `sirpi_assistant.py` - AI assistant for troubleshooting

### Utilities (`utils/`)
- `clerk_auth.py` - Authentication middleware
- `encryption.py` - Encryption for sensitive data
- `gcp_credentials_validator.py` - GCP credential validation
- `logging_config.py` - Structured logging setup

## Running the Backend

### Development
```bash
# Install dependencies
uv sync

# Run with auto-reload
uv run uvicorn src.main:app --reload --port 8000
```

### Production
```bash
# Install production dependencies only
uv sync --no-dev

# Run with multiple workers
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 4
```

## Environment Variables

Required:
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_KEY` - Supabase service role key
- `CLERK_SECRET_KEY` - Clerk authentication
- `GEMINI_API_KEY` - Google Gemini API
- `E2B_API_KEY` - E2B sandbox
- `GITHUB_APP_ID` - GitHub App ID
- `GITHUB_PRIVATE_KEY` - GitHub App private key (base64 encoded)
- `GCP_OAUTH_CLIENT_ID` - GCP OAuth client
- `GCP_OAUTH_CLIENT_SECRET` - GCP OAuth secret
- `ENCRYPTION_KEY` - 32-byte key for encrypting secrets

Optional:
- `AWS_REGION` - Default AWS region (default: us-west-2)
- `GCP_CLOUD_RUN_REGION` - GCP region (default: us-central1)
- `GCP_ARTIFACT_REGISTRY_LOCATION` - Artifact Registry location
- `E2B_TEMPLATE_ID` - Custom E2B template (optional)

## Database Migrations

Migrations are located in `database/migrations/` and should be applied in order:

1. `001_add_agent_logs.sql` - Agent logging tables
2. `002_add_workflow_logs.sql` - Workflow tracking
3. `003_add_gcp_oauth_support.sql` - GCP OAuth credentials
4. `004_add_service_account_support.sql` - (Deprecated)
5. `005_create_deployment_logs_table.sql` - Deployment logs
6. `006_update_gcp_credentials_for_oauth.sql` - OAuth updates
7. `007_add_deployment_logs_metadata.sql` - Log metadata
8. `008_remove_service_account_columns.sql` - Cleanup

Apply migrations directly to your Supabase PostgreSQL instance.

## API Documentation

Once running, visit:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Testing

```bash
# Run tests (if configured)
uv run pytest

# Type checking
uv run mypy src/

# Linting
uv run ruff check src/
```

## Dependencies

Core:
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `google-generativeai` - Gemini AI
- `e2b-code-interpreter` - E2B sandboxes
- `supabase` - Database client
- `google-cloud-*` - GCP SDKs
- `boto3` - AWS SDK

See `pyproject.toml` for complete list.

