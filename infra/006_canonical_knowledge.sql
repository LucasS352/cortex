-- 006 - Ambiente de Treinamento do CORTEX: conhecimento canônico versionado.
--
-- Princípios:
--   * Nenhum ticket vira conhecimento oficial sem aprovação humana (candidates -> items/versions).
--   * O histórico do Movidesk (refined_tickets / ticket_knowledge_nodes) continua separado.
--     Só a versão PUBLICADA é espelhada em ticket_knowledge_nodes (source_type OFFICIAL_KB,
--     source_id 'canon:<item>') para o RAG atual recuperá-la sem mudança de código.
--   * Nada é apagado: revisões geram nova versão; a anterior vira 'superseded'.
--   * Idempotente: aplicado também pelo app no startup (knowledge_training.ensure_schema).
--   * Taxonomia de módulos/subtópicos reutiliza knowledge_curation.MODULES/SUBTOPICS.

CREATE SCHEMA IF NOT EXISTS cortex_kb;

-- Identidade e classificação de um procedimento canônico.
CREATE TABLE IF NOT EXISTS cortex_kb.items (
    id              bigserial PRIMARY KEY,
    title           text NOT NULL,
    module_key      text NOT NULL,
    subtopic_key    text NOT NULL DEFAULT 'general',
    status          text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated')),
    current_version_id bigint,
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_items_module ON cortex_kb.items (module_key, subtopic_key, status);

-- Conteúdo e condições de aplicação. Uma revisão nunca sobrescreve a anterior.
CREATE TABLE IF NOT EXISTS cortex_kb.versions (
    id              bigserial PRIMARY KEY,
    item_id         bigint NOT NULL REFERENCES cortex_kb.items(id) ON DELETE CASCADE,
    version_no      integer NOT NULL,
    symptom         text NOT NULL,
    error_messages  text[] NOT NULL DEFAULT '{}',
    root_cause      text NOT NULL,
    procedure       text NOT NULL,
    customer_answer text,
    prerequisites   text,
    expected_result text,
    -- {master_version_min, master_version_max, ufs[], integrations[], tax_regime, valid_from, valid_until}
    applicability   jsonb NOT NULL DEFAULT '{}'::jsonb,
    keywords        text[] NOT NULL DEFAULT '{}',
    status          text NOT NULL CHECK (status IN ('published', 'superseded', 'deprecated')),
    change_reason   text NOT NULL,
    published_node_id bigint,
    embedding_ok    boolean NOT NULL DEFAULT false,
    approved_by     text NOT NULL,
    approved_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_kb_versions_published ON cortex_kb.versions (item_id) WHERE status = 'published';
CREATE INDEX IF NOT EXISTS idx_kb_versions_fts ON cortex_kb.versions USING gin (
    to_tsvector('portuguese', symptom || ' ' || root_cause || ' ' || procedure));

-- Evidências: quais tickets/manuais sustentam o procedimento (N tickets -> 1 item).
CREATE TABLE IF NOT EXISTS cortex_kb.sources (
    id              bigserial PRIMARY KEY,
    item_id         bigint NOT NULL REFERENCES cortex_kb.items(id) ON DELETE CASCADE,
    version_id      bigint REFERENCES cortex_kb.versions(id) ON DELETE SET NULL,
    source_type     text NOT NULL CHECK (source_type IN ('ticket', 'manual', 'kb_article', 'analyst')),
    external_ref    text NOT NULL,
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (item_id, source_type, external_ref)
);

-- Fila de curadoria: sugestões extraídas de tickets. Nunca consultadas pelo RAG.
CREATE TABLE IF NOT EXISTS cortex_kb.candidates (
    id                  bigserial PRIMARY KEY,
    ticket_external_id  integer NOT NULL,
    status              text NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'conflict', 'approved', 'merged', 'rejected')),
    usefulness_score    integer NOT NULL DEFAULT 0,
    usefulness_signals  jsonb NOT NULL DEFAULT '[]'::jsonb,
    extraction          jsonb NOT NULL,
    quality_checks      jsonb NOT NULL DEFAULT '[]'::jsonb,
    suggested_module    text NOT NULL DEFAULT 'unclassified',
    suggested_subtopic  text NOT NULL DEFAULT 'general',
    similar_items       jsonb NOT NULL DEFAULT '[]'::jsonb,
    extractor           text NOT NULL,
    created_by          text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    decided_by          text,
    decided_at          timestamptz,
    decision_note       text,
    resulting_item_id   bigint REFERENCES cortex_kb.items(id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kb_candidates_open_ticket
    ON cortex_kb.candidates (ticket_external_id) WHERE status IN ('pending', 'conflict');
CREATE INDEX IF NOT EXISTS idx_kb_candidates_queue ON cortex_kb.candidates (status, suggested_module, usefulness_score DESC);

-- Divergências entre um ticket novo e um procedimento publicado.
CREATE TABLE IF NOT EXISTS cortex_kb.conflicts (
    id              bigserial PRIMARY KEY,
    item_id         bigint NOT NULL REFERENCES cortex_kb.items(id) ON DELETE CASCADE,
    candidate_id    bigint REFERENCES cortex_kb.candidates(id) ON DELETE SET NULL,
    reason          text NOT NULL,
    status          text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'dismissed')),
    resolution_note text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_by     text,
    resolved_at     timestamptz
);
CREATE INDEX IF NOT EXISTS idx_kb_conflicts_open ON cortex_kb.conflicts (item_id) WHERE status = 'open';

-- Avaliação de uso. Alimenta a curadoria, nunca altera o conhecimento sozinha.
CREATE TABLE IF NOT EXISTS cortex_kb.feedback (
    id                  bigserial PRIMARY KEY,
    item_id             bigint NOT NULL REFERENCES cortex_kb.items(id) ON DELETE CASCADE,
    version_id          bigint REFERENCES cortex_kb.versions(id) ON DELETE SET NULL,
    rating              smallint NOT NULL CHECK (rating IN (-1, 1)),
    comment             text,
    ticket_external_id  integer,
    actor               text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Quem aprovou, alterou, mesclou, rejeitou ou inativou — e por quê.
CREATE TABLE IF NOT EXISTS cortex_kb.audit (
    id          bigserial PRIMARY KEY,
    entity      text NOT NULL,
    entity_id   bigint NOT NULL,
    action      text NOT NULL,
    actor       text NOT NULL,
    reason      text,
    before      jsonb,
    after       jsonb,
    at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_audit_entity ON cortex_kb.audit (entity, entity_id, at DESC);

-- Human feedback is scoped to one exact answer, never a whole conversation.
CREATE TABLE IF NOT EXISTS cortex_kb.taxonomy (
    key text PRIMARY KEY, label text NOT NULL, description text NOT NULL DEFAULT '',
    updated_by text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cortex_kb.answer_reviews (
    id bigserial PRIMARY KEY, ticket_external_id integer NOT NULL, action_id text NOT NULL,
    answer_text text NOT NULL, rating smallint NOT NULL CHECK (rating IN (-1,1)),
    module_key text NOT NULL, comment text NOT NULL DEFAULT '', actor text NOT NULL,
    example_ticket_external_id integer,
    candidate_id bigint REFERENCES cortex_kb.candidates(id),
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(ticket_external_id,action_id,actor)
);
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS example_ticket_external_id integer;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS proposed_title text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS proposed_answer text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS proposed_procedure text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS proposed_images jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS question_action_id text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS source_answer_action_id text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS score smallint CHECK (score >= 0 AND score <= 10);
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS subtopic_key text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS keywords text[] NOT NULL DEFAULT '{}';
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS validated_html text;
ALTER TABLE cortex_kb.answer_reviews ADD COLUMN IF NOT EXISTS is_draft boolean NOT NULL DEFAULT false;
ALTER TABLE cortex_kb.candidates ADD COLUMN IF NOT EXISTS source_action_id text;
ALTER TABLE cortex_kb.candidates ADD COLUMN IF NOT EXISTS source_question_action_id text;
ALTER TABLE cortex_kb.candidates ADD COLUMN IF NOT EXISTS source_answer_action_id text;

ALTER TABLE cortex_kb.versions ADD COLUMN IF NOT EXISTS images jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE cortex_kb.versions ADD COLUMN IF NOT EXISTS customer_answer text;

-- Peculiaridades, regressões e avisos próprios de uma versão do ERP Master.
-- O texto é escrito e aprovado por humanos e publicado no mesmo índice do RAG.
CREATE TABLE IF NOT EXISTS cortex_kb.master_release_notes (
    id              bigserial PRIMARY KEY,
    version_label   text NOT NULL,
    title           text NOT NULL,
    narrative       text NOT NULL,
    impact          text,
    workaround      text,
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','monitoring','fixed','inactive')),
    modules         text[] NOT NULL DEFAULT '{}',
    error_messages  text[] NOT NULL DEFAULT '{}',
    evidence        jsonb NOT NULL DEFAULT '[]'::jsonb,
    approved_by     text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_master_release_notes_version
    ON cortex_kb.master_release_notes (version_label, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_master_release_notes_fts ON cortex_kb.master_release_notes USING gin (
    to_tsvector('portuguese', version_label || ' ' || title || ' ' || narrative || ' ' ||
                coalesce(impact,'') || ' ' || coalesce(workaround,'')));
