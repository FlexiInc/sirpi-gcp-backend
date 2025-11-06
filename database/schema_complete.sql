-- ============================================================================
-- Sirpi Complete Database Schema - Google Cloud Edition
-- ============================================================================
-- Version: 2.0.0
-- Description: Complete schema for Sirpi AI-Native DevOps automation
-- Supports: Multi-agent collaboration, env vars, GCP/AWS deployments
-- ============================================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- USERS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    clerk_user_id TEXT UNIQUE NOT NULL,
    email TEXT NOT NULL,
    name TEXT,
    avatar_url TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_clerk_user_id ON users(clerk_user_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

COMMENT ON TABLE users IS 'User accounts managed by Clerk authentication';

-- ============================================================================
-- GITHUB INSTALLATIONS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS github_installations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    installation_id BIGINT UNIQUE NOT NULL,
    account_login TEXT NOT NULL,
    account_type TEXT NOT NULL,
    account_avatar_url TEXT,
    repositories JSONB DEFAULT '[]'::jsonb,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_github_installations_user_id ON github_installations(user_id);
CREATE INDEX IF NOT EXISTS idx_github_installations_installation_id ON github_installations(installation_id);

COMMENT ON TABLE github_installations IS 'GitHub App installations for repository access';

-- ============================================================================
-- AWS CONNECTIONS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS aws_connections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT UNIQUE NOT NULL,
    role_arn TEXT,
    external_id TEXT NOT NULL,
    account_id TEXT,
    status TEXT DEFAULT 'pending',
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aws_connections_user_id ON aws_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_aws_connections_status ON aws_connections(status);

COMMENT ON TABLE aws_connections IS 'AWS account connections for cross-account deployments';

-- ============================================================================
-- GCP CREDENTIALS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS gcp_credentials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT UNIQUE NOT NULL,
    project_id TEXT NOT NULL,
    service_account_json_encrypted TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    verified_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gcp_credentials_user_id ON gcp_credentials(user_id);
CREATE INDEX IF NOT EXISTS idx_gcp_credentials_status ON gcp_credentials(status);

COMMENT ON TABLE gcp_credentials IS 'GCP service account credentials for deployments';
COMMENT ON COLUMN gcp_credentials.service_account_json_encrypted IS 'Encrypted service account JSON key';
COMMENT ON COLUMN gcp_credentials.project_id IS 'GCP project ID from service account';

-- ============================================================================
-- PROJECTS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    repository_url TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    github_repo_id BIGINT,
    installation_id BIGINT,
    language TEXT,
    description TEXT,
    
    -- Generation status
    status TEXT DEFAULT 'pending',
    generation_count INTEGER DEFAULT 0,
    last_generated_at TIMESTAMP WITH TIME ZONE,
    
    -- Deployment status
    deployment_status TEXT,
    deployment_started_at TIMESTAMP WITH TIME ZONE,
    deployment_completed_at TIMESTAMP WITH TIME ZONE,
    deployment_error TEXT,
    
    -- Cloud connections
    aws_connection_id UUID REFERENCES aws_connections(id),
    aws_role_arn TEXT,
    gcp_connection_id UUID REFERENCES gcp_credentials(id),
    
    -- Deployment outputs
    application_url TEXT,
    terraform_outputs JSONB,
    deployment_summary JSONB,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(user_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_slug ON projects(slug);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_deployment_status ON projects(deployment_status);

COMMENT ON TABLE projects IS 'User projects with infrastructure generation and deployment tracking';

-- ============================================================================
-- PROJECT ENVIRONMENT VARIABLES TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS project_env_vars (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    key VARCHAR(255) NOT NULL,
    value_encrypted TEXT NOT NULL,
    is_secret BOOLEAN DEFAULT true,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(project_id, key)
);

CREATE INDEX IF NOT EXISTS idx_project_env_vars_project_id ON project_env_vars(project_id);

COMMENT ON TABLE project_env_vars IS 'User application environment variables (AES-256 encrypted)';
COMMENT ON COLUMN project_env_vars.value_encrypted IS 'Encrypted with ENCRYPTION_MASTER_KEY';

-- ============================================================================
-- GENERATIONS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS generations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    session_id TEXT UNIQUE NOT NULL,
    repository_url TEXT NOT NULL,
    template_type TEXT NOT NULL,
    status TEXT DEFAULT 'started',
    project_context JSONB DEFAULT '{}'::jsonb,
    s3_keys JSONB DEFAULT '[]'::jsonb,
    files JSONB DEFAULT '[]'::jsonb,
    error TEXT,
    
    -- Link to project
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    
    -- Pull request info
    pr_number INTEGER,
    pr_url TEXT,
    pr_branch TEXT,
    pr_merged BOOLEAN DEFAULT FALSE,
    pr_merged_at TIMESTAMP WITH TIME ZONE,
    
    -- Google ADK session tracking for multi-agent collaboration
    adk_session_id TEXT,
    adk_session_state JSONB DEFAULT '{}'::jsonb,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_generations_user_id ON generations(user_id);
CREATE INDEX IF NOT EXISTS idx_generations_session_id ON generations(session_id);
CREATE INDEX IF NOT EXISTS idx_generations_project_id ON generations(project_id);
CREATE INDEX IF NOT EXISTS idx_generations_adk_session_id ON generations(adk_session_id);
CREATE INDEX IF NOT EXISTS idx_generations_status ON generations(status);
CREATE INDEX IF NOT EXISTS idx_generations_files ON generations USING GIN (files);

COMMENT ON TABLE generations IS 'Infrastructure generation sessions with ADK multi-agent state';
COMMENT ON COLUMN generations.adk_session_id IS 'Google ADK session ID for agent collaboration';
COMMENT ON COLUMN generations.adk_session_state IS 'Shared state between agents (analysis, generated files, etc.)';

-- ============================================================================
-- DEPLOYMENT LOGS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS deployment_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_type TEXT NOT NULL,
    logs JSONB DEFAULT '[]'::jsonb,
    status TEXT NOT NULL,
    duration_seconds INTEGER,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(project_id, operation_type)
);

CREATE INDEX IF NOT EXISTS idx_deployment_logs_project_id ON deployment_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_deployment_logs_operation_type ON deployment_logs(operation_type);

COMMENT ON TABLE deployment_logs IS 'Deployment operation logs for audit trail';

-- ============================================================================
-- TRIGGERS FOR AUTO-UPDATE TIMESTAMPS
-- ============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_projects_updated_at BEFORE UPDATE ON projects
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_generations_updated_at BEFORE UPDATE ON generations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_github_installations_updated_at BEFORE UPDATE ON github_installations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_aws_connections_updated_at BEFORE UPDATE ON aws_connections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_project_env_vars_updated_at BEFORE UPDATE ON project_env_vars
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_gcp_credentials_updated_at BEFORE UPDATE ON gcp_credentials
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- SCHEMA COMPLETE
-- ============================================================================
