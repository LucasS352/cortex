-- Revoke existing sessions on password/access changes without storing tokens.
ALTER TABLE cortex_users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0;
