-- Company-first support context and technician workbench.
-- Idempotent so it can be applied to an existing database before deployment.

CREATE TABLE IF NOT EXISTS movidesk_company_directory (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    company_id text NOT NULL,
    company_name text NOT NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, company_id)
);

CREATE TABLE IF NOT EXISTS movidesk_company_tickets (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    company_id text NOT NULL,
    ticket_external_id bigint NOT NULL,
    PRIMARY KEY (tenant_id, company_id, ticket_external_id)
);

CREATE INDEX IF NOT EXISTS idx_movidesk_company_directory_name
    ON movidesk_company_directory USING gin (company_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_movidesk_company_tickets_ticket
    ON movidesk_company_tickets (tenant_id, ticket_external_id);

CREATE TABLE IF NOT EXISTS company_context_profiles (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    movidesk_company_id text NOT NULL,
    company_name text NOT NULL,
    facts jsonb NOT NULL DEFAULT '[]'::jsonb,
    version integer NOT NULL DEFAULT 1,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, movidesk_company_id)
);

CREATE TABLE IF NOT EXISTS company_environment_links (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    movidesk_company_id text NOT NULL,
    environment_id uuid NOT NULL REFERENCES master_environments(id),
    linked_by text NOT NULL,
    linked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, environment_id),
    FOREIGN KEY (tenant_id, movidesk_company_id)
        REFERENCES company_context_profiles(tenant_id, movidesk_company_id)
);
CREATE INDEX IF NOT EXISTS idx_company_environment_links_company
    ON company_environment_links (tenant_id, movidesk_company_id);

CREATE TABLE IF NOT EXISTS technician_conversations (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    movidesk_company_id text NOT NULL,
    contact_name text NOT NULL,
    subject text NOT NULL,
    technician text NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','archived')),
    confirmed_action text NOT NULL DEFAULT '',
    confirmed_result text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, movidesk_company_id)
        REFERENCES company_context_profiles(tenant_id, movidesk_company_id)
);
CREATE INDEX IF NOT EXISTS idx_technician_conversations_company
    ON technician_conversations (tenant_id, movidesk_company_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS technician_conversation_messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES technician_conversations(id),
    role text NOT NULL CHECK (role IN ('technician','assistant')),
    content text NOT NULL,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_technician_conversation_messages
    ON technician_conversation_messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS technician_ticket_drafts (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES technician_conversations(id),
    kind text NOT NULL CHECK (kind IN ('SUP','DEV')),
    subject text NOT NULL,
    body text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    movidesk_ticket_id text,
    UNIQUE (conversation_id, kind)
);

-- Build the company lookup from the raw Movidesk payload already imported.
WITH organizations AS (
    SELECT raw.tenant_id, raw.external_id,
           COALESCE(client->'organization'->>'id',
                    CASE WHEN client->>'personType'='2' THEN client->>'id' END) AS company_id,
           COALESCE(client->'organization'->>'businessName',
                    CASE WHEN client->>'personType'='2' THEN client->>'businessName' END) AS company_name,
           raw.ingested_at
    FROM raw_tickets raw
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(raw.payload->'clients','[]'::jsonb)) client
)
INSERT INTO movidesk_company_directory (tenant_id, company_id, company_name, last_seen_at)
SELECT tenant_id, company_id, max(company_name), max(ingested_at)
FROM organizations WHERE company_id IS NOT NULL AND company_name IS NOT NULL
GROUP BY tenant_id, company_id
ON CONFLICT (tenant_id, company_id) DO UPDATE
SET company_name=EXCLUDED.company_name, last_seen_at=EXCLUDED.last_seen_at;

WITH organizations AS (
    SELECT DISTINCT raw.tenant_id, raw.external_id,
           COALESCE(client->'organization'->>'id',
                    CASE WHEN client->>'personType'='2' THEN client->>'id' END) AS company_id
    FROM raw_tickets raw
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(raw.payload->'clients','[]'::jsonb)) client
)
INSERT INTO movidesk_company_tickets (tenant_id, company_id, ticket_external_id)
SELECT tenant_id, company_id, external_id FROM organizations WHERE company_id IS NOT NULL
ON CONFLICT DO NOTHING;
