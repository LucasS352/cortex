CREATE TABLE IF NOT EXISTS movidesk_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed')),
    previous_max_id INTEGER NOT NULL,
    final_max_id INTEGER,
    new_count INTEGER NOT NULL DEFAULT 0,
    older_open_count INTEGER NOT NULL DEFAULT 0
);

-- Version 2 reads both /tickets and /tickets/past. Older snapshots undercounted
-- tickets without recent updates and must not be graphed as the same series.
ALTER TABLE movidesk_sync_runs ADD COLUMN IF NOT EXISTS coverage_version INTEGER NOT NULL DEFAULT 1;

-- A ticket can disappear from both API routes (for example after deletion).
-- Keep that exception explicit instead of inventing a resolved date.
CREATE TABLE IF NOT EXISTS movidesk_sync_unavailable (
    run_id BIGINT NOT NULL REFERENCES movidesk_sync_runs(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    external_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (run_id, external_id)
);

CREATE TABLE IF NOT EXISTS movidesk_open_snapshot (
    run_id BIGINT NOT NULL REFERENCES movidesk_sync_runs(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    external_id INTEGER NOT NULL,
    dashboard_data JSONB,
    PRIMARY KEY (run_id, external_id)
);

ALTER TABLE movidesk_open_snapshot ADD COLUMN IF NOT EXISTS dashboard_data JSONB;

CREATE OR REPLACE VIEW current_open_tickets AS
SELECT r.*, run.completed_at AS current_as_of
FROM movidesk_sync_runs run
JOIN movidesk_open_snapshot snapshot ON snapshot.run_id = run.id
JOIN refined_tickets r ON r.tenant_id = snapshot.tenant_id
    AND r.external_id = snapshot.external_id
WHERE run.id = (
    SELECT id FROM movidesk_sync_runs WHERE status = 'completed'
    ORDER BY completed_at DESC, id DESC LIMIT 1
) AND r.base_status NOT IN ('Resolved', 'Closed', 'Canceled');

-- Backfill the latest completed run when upgrading an existing installation.
UPDATE movidesk_open_snapshot s
SET dashboard_data = jsonb_build_object(
    'subject', r.subject, 'category', r.category, 'urgency', r.urgency,
    'status', r.status, 'base_status', r.base_status,
    'created_date', r.created_date, 'problem_statement', r.problem_statement,
    'technical_narrative', r.technical_narrative,
    'client_business_name', r.client_business_name,
    'clients', w.payload->'clients', 'owner', w.payload->'owner',
    'owner_team', w.payload->>'ownerTeam')
FROM refined_tickets r
JOIN raw_tickets w ON w.id = r.raw_ticket_id
WHERE s.run_id = (
    SELECT id FROM movidesk_sync_runs WHERE status = 'completed'
    ORDER BY completed_at DESC, id DESC LIMIT 1
)
  AND s.dashboard_data IS NULL
  AND s.tenant_id = r.tenant_id AND s.external_id = r.external_id
  AND r.base_status NOT IN ('Resolved', 'Closed', 'Canceled');

CREATE OR REPLACE VIEW current_open_ticket_facts AS
SELECT s.tenant_id, s.external_id, s.dashboard_data, run.completed_at AS current_as_of
FROM movidesk_sync_runs run
JOIN movidesk_open_snapshot s ON s.run_id = run.id
WHERE run.id = (
    SELECT id FROM movidesk_sync_runs WHERE status = 'completed'
    ORDER BY completed_at DESC, id DESC LIMIT 1
) AND s.dashboard_data IS NOT NULL;
