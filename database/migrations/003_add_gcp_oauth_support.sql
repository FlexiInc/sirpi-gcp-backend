-- Migration 003: Add GCP OAuth 2.0 support
-- Adds OAuth token storage to existing gcp_credentials table
-- Creates oauth_states table for CSRF protection

-- ============================================================================
-- ALTER GCP_CREDENTIALS TABLE FOR OAUTH
-- ============================================================================

-- Add OAuth token columns
ALTER TABLE gcp_credentials 
ADD COLUMN IF NOT EXISTS access_token TEXT,
ADD COLUMN IF NOT EXISTS refresh_token TEXT,
ADD COLUMN IF NOT EXISTS token_expiry TIMESTAMPTZ;

-- Make service_account_json_encrypted nullable (OAuth doesn't use it)
ALTER TABLE gcp_credentials 
ALTER COLUMN service_account_json_encrypted DROP NOT NULL;

-- Drop the UNIQUE constraint on user_id (users can have multiple GCP projects)
ALTER TABLE gcp_credentials 
DROP CONSTRAINT IF EXISTS gcp_credentials_user_id_key;

-- Add composite unique constraint (one credential per user per project)
ALTER TABLE gcp_credentials 
ADD CONSTRAINT gcp_credentials_user_project_unique 
UNIQUE (user_id, project_id);

-- Update comments
COMMENT ON COLUMN gcp_credentials.access_token IS 'OAuth 2.0 access token (encrypted)';
COMMENT ON COLUMN gcp_credentials.refresh_token IS 'OAuth 2.0 refresh token (encrypted)';
COMMENT ON COLUMN gcp_credentials.token_expiry IS 'Access token expiry timestamp';
COMMENT ON COLUMN gcp_credentials.service_account_json_encrypted IS 'Legacy: Service account JSON (for non-OAuth flow)';

-- ============================================================================
-- CREATE OAUTH_STATES TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_user_id ON oauth_states(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expires_at ON oauth_states(expires_at);

COMMENT ON TABLE oauth_states IS 'Temporary OAuth state storage for CSRF protection';
COMMENT ON COLUMN oauth_states.state IS 'Random state parameter for OAuth flow';
COMMENT ON COLUMN oauth_states.expires_at IS 'State expires after 10 minutes';

-- ============================================================================
-- CLEANUP FUNCTION FOR EXPIRED OAUTH STATES
-- ============================================================================

CREATE OR REPLACE FUNCTION cleanup_expired_oauth_states()
RETURNS void AS $$
BEGIN
    DELETE FROM oauth_states WHERE expires_at < NOW();
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_expired_oauth_states IS 'Call periodically to remove expired OAuth states';

ALTER TABLE projects 
ADD COLUMN IF NOT EXISTS cloud_provider TEXT DEFAULT 'gcp';
-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
