-- Migration: Add agent_logs table for persistent agent thinking logs
-- Supports real-time SSE streaming via PostgreSQL LISTEN/NOTIFY

CREATE TABLE IF NOT EXISTS agent_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    stage TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Foreign key to generations
    generation_id UUID REFERENCES generations(id) ON DELETE CASCADE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_session_id ON agent_logs(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_generation_id ON agent_logs(generation_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_timestamp ON agent_logs(timestamp);

COMMENT ON TABLE agent_logs IS 'Agent thinking logs for real-time transparency and debugging';
COMMENT ON COLUMN agent_logs.agent IS 'Agent name: orchestrator, code_analyzer, dockerfile_generator, etc.';
COMMENT ON COLUMN agent_logs.stage IS 'Stage: starting, analyzing, completed, failed, etc.';
COMMENT ON COLUMN agent_logs.content IS 'Human-readable log message';

-- Function to notify on new agent log
CREATE OR REPLACE FUNCTION notify_agent_log()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'agent_logs_' || NEW.session_id,
        json_build_object(
            'id', NEW.id,
            'agent', NEW.agent,
            'stage', NEW.stage,
            'content', NEW.content,
            'timestamp', NEW.timestamp
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to notify on insert
CREATE TRIGGER agent_logs_notify
AFTER INSERT ON agent_logs
FOR EACH ROW
EXECUTE FUNCTION notify_agent_log();

COMMENT ON FUNCTION notify_agent_log IS 'PostgreSQL LISTEN/NOTIFY trigger for real-time SSE streaming';
