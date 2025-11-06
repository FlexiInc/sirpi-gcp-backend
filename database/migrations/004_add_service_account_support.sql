-- Migration 004: Add Service Account Support for Deployment
-- Creates service accounts for users to enable long-lived, secure deployments
-- Service accounts are created in user's GCP project via OAuth, then used for all deployments

-- ============================================================================
-- ADD SERVICE ACCOUNT COLUMNS TO GCP_CREDENTIALS
-- ============================================================================

-- Add service account metadata
ALTER TABLE gcp_credentials 
ADD COLUMN IF NOT EXISTS sirpi_service_account_email TEXT,
ADD COLUMN IF NOT EXISTS sirpi_service_account_key_encrypted TEXT,
ADD COLUMN IF NOT EXISTS service_account_created_at TIMESTAMPTZ;

-- Add index for faster SA lookups
CREATE INDEX IF NOT EXISTS idx_gcp_creds_sa_email 
ON gcp_credentials(sirpi_service_account_email);

-- Add comments
COMMENT ON COLUMN gcp_credentials.sirpi_service_account_email 
IS 'Service account created by Sirpi in user''s GCP project for deployments';

COMMENT ON COLUMN gcp_credentials.sirpi_service_account_key_encrypted 
IS 'Encrypted JSON key for the Sirpi service account';

COMMENT ON COLUMN gcp_credentials.service_account_created_at 
IS 'Timestamp when service account was created';

-- ============================================================================
-- MIGRATION COMPLETE
-- ============================================================================
