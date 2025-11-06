-- Migration 008: Remove service account columns (no longer used)
-- Sirpi uses OAuth tokens with automatic refresh instead of service accounts

-- Drop service account related columns from gcp_credentials
ALTER TABLE gcp_credentials 
DROP COLUMN IF EXISTS sirpi_service_account_email,
DROP COLUMN IF EXISTS sirpi_service_account_key_encrypted,
DROP COLUMN IF EXISTS service_account_created_at;

-- Add comment explaining the architecture
COMMENT ON TABLE gcp_credentials IS 'Stores OAuth tokens for GCP authentication. Tokens are automatically refreshed - no service accounts needed.';
