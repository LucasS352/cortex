-- ============================================================
-- DeskCortex - Schema DDL (Sprint 1 - ELT Staging)
-- PostgreSQL 16 + pgvector
-- ============================================================

-- 1. Extensões necessárias
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ============================================================
-- 2. TENANTS (Organizações SaaS Multi-Tenant)
-- ============================================================
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(100) UNIQUE NOT NULL,
    erp_system_name VARCHAR(100) DEFAULT 'Master',
    plan_tier       VARCHAR(50)  DEFAULT 'pro',
    is_active       BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

COMMENT ON TABLE tenants IS 'Organizações clientes do DeskCortex (multi-tenant)';

-- ============================================================
-- 3. CONNECTORS (Tokens e Checkpoints de Sincronização)
-- ============================================================
CREATE TABLE connectors (
    id                      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id               UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    provider                VARCHAR(50)  NOT NULL DEFAULT 'movidesk',
    api_base_url            VARCHAR(255) DEFAULT 'https://api.movidesk.com/public/v1',
    is_active               BOOLEAN      DEFAULT TRUE,
    -- Estado da sincronização
    sync_status             VARCHAR(50)  DEFAULT 'idle',  -- idle, in_progress, completed, error
    sync_phase              VARCHAR(20)  DEFAULT 'past',  -- past, recent, incremental
    -- Checkpoints de paginação (retomada sem perda)
    last_synced_at          TIMESTAMPTZ,
    last_synced_ticket_id   INT          DEFAULT 0,
    checkpoint_endpoint     VARCHAR(30)  DEFAULT '/tickets/past',
    checkpoint_skip         INT          DEFAULT 0,
    total_tickets_ingested  INT          DEFAULT 0,
    -- Metadados
    last_error_message      TEXT,
    created_at              TIMESTAMPTZ  DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  DEFAULT NOW()
);

COMMENT ON TABLE connectors IS 'Tokens de API e estado de sincronização por tenant/provider';
COMMENT ON COLUMN connectors.checkpoint_skip IS 'Offset OData ($skip) para retomada de paginação';
COMMENT ON COLUMN connectors.sync_phase IS 'Fase atual: past=histórico >90d, recent=últimos 90d, incremental=delta contínuo';

-- ============================================================
-- 4. RAW_TICKETS (Staging ELT - JSON Bruto Intacto)
-- ============================================================
CREATE TABLE raw_tickets (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID         NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_provider VARCHAR(50)  DEFAULT 'movidesk',
    external_id     INT          NOT NULL,
    payload         JSONB        NOT NULL,
    created_date    TIMESTAMPTZ,          -- Extraído do JSON: createdDate
    ingested_at     TIMESTAMPTZ  DEFAULT NOW(),
    -- Controle de processamento futuro (Sprint 2+)
    processing_status VARCHAR(50) DEFAULT 'pending',  -- pending, processing, done, error
    processed_at    TIMESTAMPTZ,
    -- Constraint de idempotência: mesmo ticket nunca duplica
    CONSTRAINT uq_tenant_provider_external UNIQUE (tenant_id, source_provider, external_id)
);

COMMENT ON TABLE raw_tickets IS 'Staging ELT: JSON bruto completo da API Movidesk, sem transformação';
COMMENT ON COLUMN raw_tickets.payload IS 'JSON completo do ticket incluindo actions, clients e customFieldValues';

-- ============================================================
-- 5. INGESTION_LOG (Auditoria de cada request à API)
-- ============================================================
CREATE TABLE ingestion_log (
    id              BIGSERIAL PRIMARY KEY,
    connector_id    UUID REFERENCES connectors(id),
    endpoint        VARCHAR(50),
    page_skip       INT,
    tickets_fetched INT          DEFAULT 0,
    status          VARCHAR(20)  NOT NULL,  -- success, error, rate_limited
    http_status     INT,
    error_message   TEXT,
    duration_ms     INT,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

COMMENT ON TABLE ingestion_log IS 'Log de auditoria de cada requisição à API do Movidesk';

-- ============================================================
-- 6. ÍNDICES DE PERFORMANCE
-- ============================================================
CREATE INDEX idx_raw_tickets_tenant        ON raw_tickets(tenant_id);
CREATE INDEX idx_raw_tickets_external      ON raw_tickets(tenant_id, external_id);
CREATE INDEX idx_raw_tickets_created       ON raw_tickets(created_date);
CREATE INDEX idx_raw_tickets_processing    ON raw_tickets(processing_status) WHERE processing_status = 'pending';
CREATE INDEX idx_connectors_tenant         ON connectors(tenant_id, provider);
CREATE INDEX idx_ingestion_log_connector   ON ingestion_log(connector_id, created_at DESC);

-- ============================================================
-- 7. SEED: Tenant Alternativa Sistemas (Tenant #1)
-- ============================================================
INSERT INTO tenants (name, slug, erp_system_name)
VALUES ('Alternativa Sistemas LTDA', 'alternativa', 'Master')
ON CONFLICT (slug) DO NOTHING;

-- Conector Movidesk para a Alternativa (sem token no banco - fica no .env)
INSERT INTO connectors (tenant_id, provider, api_base_url)
SELECT id, 'movidesk', 'https://api.movidesk.com/public/v1'
FROM tenants WHERE slug = 'alternativa'
ON CONFLICT DO NOTHING;

-- ============================================================
-- Verificação final
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE '✅ DeskCortex schema criado com sucesso!';
    RAISE NOTICE '   → tenants, connectors, raw_tickets, ingestion_log';
    RAISE NOTICE '   → pgvector extension: %', (SELECT extversion FROM pg_extension WHERE extname = 'vector');
END $$;
