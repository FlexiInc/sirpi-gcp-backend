-- Migration 005: Create deployment_logs table
-- Stores logs for build, plan, apply, destroy operations per project

CREATE TABLE IF NOT EXISTS deployment_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_type TEXT NOT NULL CHECK (operation_type IN ('build_image', 'plan', 'apply', 'destroy')),
    logs JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('success', 'error', 'running')),
    error_message TEXT,
    duration_seconds INTEGER,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    -- One set of logs per operation per project
    UNIQUE(project_id, operation_type)
);


ALTER TABLE deployment_logs
ADD CONSTRAINT unique_project_operation UNIQUE (project_id, operation_type);


-- Indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_deployment_logs_project ON deployment_logs(project_id);
CREATE INDEX IF NOT EXISTS idx_deployment_logs_operation ON deployment_logs(operation_type);
CREATE INDEX IF NOT EXISTS idx_deployment_logs_completed ON deployment_logs(completed_at DESC);

-- Comments
COMMENT ON TABLE deployment_logs IS 'Stores logs for deployment operations (build, plan, apply, destroy)';
COMMENT ON COLUMN deployment_logs.operation_type IS 'Type of operation: build_image, plan, apply, destroy';
COMMENT ON COLUMN deployment_logs.logs IS 'Array of log messages';
COMMENT ON COLUMN deployment_logs.status IS 'Operation status: success, error, running';
COMMENT ON COLUMN deployment_logs.completed_at IS 'When the operation completed';
