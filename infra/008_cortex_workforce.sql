-- 008_cortex_workforce.sql
-- Idempotent schema for Cortex Workforce: users, work sessions, intervals, and audit.

CREATE TABLE IF NOT EXISTS cortex_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(80) UNIQUE NOT NULL,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(160) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(30) NOT NULL CHECK (role IN ('super_admin', 'director', 'support_supervisor', 'support_analyst')),
    movidesk_person_id VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT true,
    must_change_password BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cortex_users_role ON cortex_users (role);
CREATE INDEX IF NOT EXISTS idx_cortex_users_active ON cortex_users (is_active);

CREATE TABLE IF NOT EXISTS cortex_work_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES cortex_users(id) ON DELETE CASCADE,
    ticket_id INTEGER NOT NULL,
    ticket_subject TEXT,
    client_name TEXT,
    service_full TEXT,
    category TEXT,
    movidesk_status TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'paused', 'completed', 'superseded', 'discarded')),
    completion_outcome VARCHAR(30) CHECK (completion_outcome IN ('responded', 'resolved', 'waiting_client', 'forwarded_dev', 'internal_action')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cortex_active_session_per_user 
    ON cortex_work_sessions (user_id) 
    WHERE status IN ('active', 'paused');

CREATE INDEX IF NOT EXISTS idx_cortex_work_sessions_user_date 
    ON cortex_work_sessions (user_id, started_at);

CREATE INDEX IF NOT EXISTS idx_cortex_work_sessions_ticket 
    ON cortex_work_sessions (ticket_id);

CREATE TABLE IF NOT EXISTS cortex_work_intervals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES cortex_work_sessions(id) ON DELETE CASCADE,
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('work', 'interruption')),
    reason VARCHAR(30) CHECK (reason IN ('phone_call', 'colleague_help', 'incident', 'personal_break')),
    details VARCHAR(200),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ,
    CONSTRAINT chk_cortex_interval_dates CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_cortex_open_interval_per_session 
    ON cortex_work_intervals (session_id) 
    WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_cortex_work_intervals_session 
    ON cortex_work_intervals (session_id);

CREATE TABLE IF NOT EXISTS cortex_user_audit (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor VARCHAR(80) NOT NULL,
    action VARCHAR(80) NOT NULL,
    target_user_id UUID REFERENCES cortex_users(id) ON DELETE SET NULL,
    before_state JSONB,
    after_state JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cortex_user_audit_created 
    ON cortex_user_audit (created_at DESC);

-- Initial Super Admin: lucas.souza (@alternativa352)
INSERT INTO cortex_users (
    username,
    name,
    email,
    password_hash,
    role,
    movidesk_person_id,
    is_active,
    must_change_password
) VALUES (
    'lucas.souza',
    'Lucas Souza',
    'lucas.souza@alternativasistemas.com.br',
    'scrypt$16384$8$1$67d6c4163c67b9ffb9f8c7145b7c546e$c51a4f5bde525b78bcbb17fb3ae77f94dce4857b98303c41f6f13fda87f0fb75a090d6f5870cf7b8b087db7ae42db28599ce1293b91baf9e6f1e61637d26e3f3',
    'super_admin',
    NULL,
    true,
    false
) ON CONFLICT (username) DO UPDATE SET
    password_hash = EXCLUDED.password_hash,
    role = EXCLUDED.role,
    is_active = true,
    updated_at = now();
