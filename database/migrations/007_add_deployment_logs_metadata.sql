-- Migration 007: Add metadata column to deployment_logs
-- Stores additional structured data like image URIs, outputs, etc.

ALTER TABLE deployment_logs 
ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}'::jsonb;

-- Index for querying metadata
CREATE INDEX IF NOT EXISTS idx_deployment_logs_metadata ON deployment_logs USING GIN (metadata);

-- Comment
COMMENT ON COLUMN deployment_logs.metadata IS 'Additional structured metadata (e.g., image_uri, outputs)';
