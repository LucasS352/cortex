-- Run outside a transaction to keep writes available during the build.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_refined_flow_created ON refined_tickets(created_date);
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_refined_flow_resolved ON refined_tickets(resolved_date) WHERE resolved_date IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_refined_flow_closed ON refined_tickets(closed_date) WHERE closed_date IS NOT NULL;
