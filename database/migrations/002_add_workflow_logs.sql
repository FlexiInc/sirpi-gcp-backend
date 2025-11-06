-- Migration: Add workflow_logs table for generation/workflow logs
-- Separate from agent_logs (which are AI thinking logs)
-- These are high-level workflow execution logs

CREATE TABLE IF NOT EXISTS workflow_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id TEXT NOT NULL,
    stage TEXT NOT NULL, -- 'analyze', 'generate', 'upload', 'complete'
    logs JSONB DEFAULT '[]'::jsonb,
    status TEXT NOT NULL, -- 'running', 'success', 'error'
    duration_seconds INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    
    -- Foreign key to generations
    generation_id UUID REFERENCES generations(id) ON DELETE CASCADE,
    
    -- One log record per stage per session
    UNIQUE(session_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_workflow_logs_session_id ON workflow_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_workflow_logs_generation_id ON workflow_logs(generation_id);
CREATE INDEX IF NOT EXISTS idx_workflow_logs_stage ON workflow_logs(stage);

COMMENT ON TABLE workflow_logs IS 'High-level workflow execution logs organized by stage';
COMMENT ON COLUMN workflow_logs.stage IS 'Workflow stage: analyze, generate, upload, complete';
COMMENT ON COLUMN workflow_logs.logs IS 'Array of log messages for this stage';
