-- Migration: Update gcp_credentials table for OAuth 2.0 flow
-- Adds columns for OAuth tokens and service account management

-- Remove the old constraint if it exists
ALTER TABLE gcp_credentials DROP CONSTRAINT IF EXISTS gcp_credentials_user_id_key;

-- Add new columns for OAuth tokens (if they don't exist)
ALTER TABLE gcp_credentials 
ADD COLUMN IF NOT EXISTS access_token TEXT,
ADD COLUMN IF NOT EXISTS refresh_token TEXT,
ADD COLUMN IF NOT EXISTS token_expiry TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS sirpi_service_account_email TEXT,
ADD COLUMN IF NOT EXISTS sirpi_service_account_key_encrypted TEXT,
ADD COLUMN IF NOT EXISTS service_account_created_at TIMESTAMP WITH TIME ZONE;

-- Make service_account_json_encrypted nullable (since we now use OAuth tokens)
ALTER TABLE gcp_credentials 
ALTER COLUMN service_account_json_encrypted DROP NOT NULL;

-- Update unique constraint to allow multiple projects per user
ALTER TABLE gcp_credentials 
DROP CONSTRAINT IF EXISTS gcp_credentials_user_id_key CASCADE;

-- Add composite unique constraint for user + project
ALTER TABLE gcp_credentials
DROP CONSTRAINT IF EXISTS unique_user_project;

ALTER TABLE gcp_credentials
ADD CONSTRAINT unique_user_project UNIQUE (user_id, project_id);

-- Add oauth_states table for CSRF protection
CREATE TABLE IF NOT EXISTS oauth_states (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    state TEXT NOT NULL UNIQUE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oauth_states_user_id ON oauth_states(user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_states_state ON oauth_states(state);
CREATE INDEX IF NOT EXISTS idx_oauth_states_expires_at ON oauth_states(expires_at);

COMMENT ON TABLE oauth_states IS 'Temporary OAuth state tokens for CSRF protection';

-- Drop existing cleanup function and trigger if they exist
DROP TRIGGER IF EXISTS cleanup_oauth_states_trigger ON oauth_states;
DROP FUNCTION IF EXISTS cleanup_expired_oauth_states();

-- Add cleanup trigger for expired states (optional but recommended)
CREATE OR REPLACE FUNCTION cleanup_expired_oauth_states()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM oauth_states WHERE expires_at < NOW() - INTERVAL '1 hour';
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER cleanup_oauth_states_trigger
AFTER INSERT ON oauth_states
EXECUTE FUNCTION cleanup_expired_oauth_states();

COMMENT ON COLUMN gcp_credentials.access_token IS 'Encrypted OAuth 2.0 access token';
COMMENT ON COLUMN gcp_credentials.refresh_token IS 'Encrypted OAuth 2.0 refresh token';
COMMENT ON COLUMN gcp_credentials.sirpi_service_account_email IS 'Email of Sirpi service account created in user project';
COMMENT ON COLUMN gcp_credentials.sirpi_service_account_key_encrypted IS 'Encrypted service account key for deployment operations';
