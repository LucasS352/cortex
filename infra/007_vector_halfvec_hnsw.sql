-- 007 - Índice semântico para embeddings Gemini de 3.072 dimensões.
--
-- pgvector permite HNSW em vector apenas até 2.000 dimensões. A coluna
-- ticket_knowledge_nodes.embedding já é vector(3072), portanto o índice é
-- construído em halfvec(3072), sem alterar nem reprocessar os vetores atuais.
--
-- IMPORTANTE: CREATE INDEX CONCURRENTLY não deve ser executado dentro de uma
-- transação explícita. Em uma base existente, aplique este arquivo com psql
-- após backup validado. Em falha, confira pg_index.indisvalid antes de repetir.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_kn_embedding_halfvec_hnsw
    ON public.ticket_knowledge_nodes
    USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64)
    WHERE embedding_generated IS TRUE AND embedding IS NOT NULL;
