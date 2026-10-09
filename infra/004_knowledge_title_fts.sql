-- Reproduce the index already installed locally. This is full-text search,
-- not a semantic embedding/vector index. IF NOT EXISTS makes reruns safe.
CREATE INDEX IF NOT EXISTS idx_kn_title_fts ON public.ticket_knowledge_nodes
USING gin (to_tsvector('portuguese', COALESCE(title, '')));
