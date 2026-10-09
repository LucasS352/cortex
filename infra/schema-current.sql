--
-- PostgreSQL database dump
--

\restrict 2vsUoBabtJ4BZC0HDnEbZ9NkjmmwJefy4I4KPVtViYaA8lRYk3hLdLeOyUSeqmg

-- Dumped from database version 16.15 (Debian 16.15-1.pgdg12+2)
-- Dumped by pg_dump version 16.15 (Debian 16.15-1.pgdg12+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: knowledge_curation; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA knowledge_curation;


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: unaccent; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;


--
-- Name: EXTENSION unaccent; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION unaccent IS 'text search dictionary that removes accents';


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: card_versions; Type: TABLE; Schema: knowledge_curation; Owner: -
--

CREATE TABLE knowledge_curation.card_versions (
    tenant_id uuid NOT NULL,
    card_key text NOT NULL,
    revision text NOT NULL,
    document jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cards; Type: TABLE; Schema: knowledge_curation; Owner: -
--

CREATE TABLE knowledge_curation.cards (
    tenant_id uuid NOT NULL,
    card_key text NOT NULL,
    title text NOT NULL,
    module text NOT NULL,
    kind text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    revision text NOT NULL,
    document jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cards_kind_check CHECK ((kind = ANY (ARRAY['curated'::text, 'candidate'::text]))),
    CONSTRAINT cards_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'approved'::text, 'rejected'::text, 'needs_review'::text])))
);


--
-- Name: reviews; Type: TABLE; Schema: knowledge_curation; Owner: -
--

CREATE TABLE knowledge_curation.reviews (
    id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    card_key text NOT NULL,
    revision text NOT NULL,
    reviewer text NOT NULL,
    decision text NOT NULL,
    note text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT reviews_decision_check CHECK ((decision = ANY (ARRAY['approved'::text, 'rejected'::text, 'needs_review'::text])))
);


--
-- Name: reviews_id_seq; Type: SEQUENCE; Schema: knowledge_curation; Owner: -
--

ALTER TABLE knowledge_curation.reviews ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME knowledge_curation.reviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: runs; Type: TABLE; Schema: knowledge_curation; Owner: -
--

CREATE TABLE knowledge_curation.runs (
    id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    metrics jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: runs_id_seq; Type: SEQUENCE; Schema: knowledge_curation; Owner: -
--

ALTER TABLE knowledge_curation.runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME knowledge_curation.runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: sources; Type: TABLE; Schema: knowledge_curation; Owner: -
--

CREATE TABLE knowledge_curation.sources (
    tenant_id uuid NOT NULL,
    source_key text NOT NULL,
    title text NOT NULL,
    source_type text NOT NULL,
    external_id integer NOT NULL,
    url text NOT NULL,
    source_date text,
    content_hash text NOT NULL,
    segments jsonb NOT NULL,
    signatures jsonb NOT NULL,
    flags jsonb NOT NULL,
    refreshed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT sources_source_type_check CHECK ((source_type = ANY (ARRAY['ticket'::text, 'kb'::text])))
);


--
-- Name: connectors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connectors (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    provider character varying(50) DEFAULT 'movidesk'::character varying NOT NULL,
    api_base_url character varying(255) DEFAULT 'https://api.movidesk.com/public/v1'::character varying,
    is_active boolean DEFAULT true,
    sync_status character varying(50) DEFAULT 'idle'::character varying,
    sync_phase character varying(20) DEFAULT 'past'::character varying,
    last_synced_at timestamp with time zone,
    last_synced_ticket_id integer DEFAULT 0,
    checkpoint_endpoint character varying(30) DEFAULT '/tickets/past'::character varying,
    checkpoint_skip integer DEFAULT 0,
    total_tickets_ingested integer DEFAULT 0,
    last_error_message text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE connectors; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.connectors IS 'Tokens de API e estado de sincronização por tenant/provider';


--
-- Name: COLUMN connectors.sync_phase; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.connectors.sync_phase IS 'Fase atual: past=histórico >90d, recent=últimos 90d, incremental=delta contínuo';


--
-- Name: COLUMN connectors.checkpoint_skip; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.connectors.checkpoint_skip IS 'Offset OData ($skip) para retomada de paginação';


--
-- Name: copilot_feedback_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.copilot_feedback_log (
    id bigint NOT NULL,
    tenant_id uuid,
    node_id bigint,
    query text NOT NULL,
    diagnosis_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    feedback_type character varying(20) NOT NULL,
    edited_content text,
    agent_name character varying(100),
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT copilot_feedback_log_feedback_type_check CHECK (((feedback_type)::text = ANY ((ARRAY['APPROVED'::character varying, 'EDITED'::character varying, 'REJECTED'::character varying])::text[])))
);


--
-- Name: copilot_feedback_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.copilot_feedback_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: copilot_feedback_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.copilot_feedback_log_id_seq OWNED BY public.copilot_feedback_log.id;


--
-- Name: copilot_feedback_metrics; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.copilot_feedback_metrics AS
 SELECT feedback_type,
    count(*) AS total,
    count(DISTINCT agent_name) AS unique_agents,
    date_trunc('day'::text, created_at) AS day
   FROM public.copilot_feedback_log
  GROUP BY feedback_type, (date_trunc('day'::text, created_at))
  ORDER BY (date_trunc('day'::text, created_at)) DESC, feedback_type;


--
-- Name: movidesk_open_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.movidesk_open_snapshot (
    run_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    external_id integer NOT NULL,
    dashboard_data jsonb
);


--
-- Name: movidesk_sync_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.movidesk_sync_runs (
    id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    status text NOT NULL,
    previous_max_id integer NOT NULL,
    final_max_id integer,
    new_count integer DEFAULT 0 NOT NULL,
    older_open_count integer DEFAULT 0 NOT NULL,
    coverage_version integer DEFAULT 1 NOT NULL,
    CONSTRAINT movidesk_sync_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'completed'::text, 'partial'::text, 'failed'::text])))
);


--
-- Name: current_open_ticket_facts; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.current_open_ticket_facts AS
 SELECT s.tenant_id,
    s.external_id,
    s.dashboard_data,
    run.completed_at AS current_as_of
   FROM (public.movidesk_sync_runs run
     JOIN public.movidesk_open_snapshot s ON ((s.run_id = run.id)))
  WHERE ((run.id = ( SELECT movidesk_sync_runs.id
           FROM public.movidesk_sync_runs
          WHERE (movidesk_sync_runs.status = 'completed'::text)
          ORDER BY movidesk_sync_runs.completed_at DESC, movidesk_sync_runs.id DESC
         LIMIT 1)) AND (s.dashboard_data IS NOT NULL));


--
-- Name: refined_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refined_tickets (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    raw_ticket_id uuid,
    external_id integer NOT NULL,
    subject text NOT NULL,
    service_full text,
    service_first_level character varying(255),
    category character varying(128),
    urgency character varying(128),
    status character varying(128),
    base_status character varying(128),
    client_business_name character varying(255),
    client_organization_name character varying(255),
    client_email character varying(255),
    created_date timestamp with time zone,
    resolved_date timestamp with time zone,
    closed_date timestamp with time zone,
    total_actions integer DEFAULT 0,
    agent_actions_count integer DEFAULT 0,
    client_actions_count integer DEFAULT 0,
    problem_statement text,
    technical_narrative text,
    solution_statement text,
    detected_modules jsonb DEFAULT '[]'::jsonb,
    detected_error_codes jsonb DEFAULT '[]'::jsonb,
    technical_score integer DEFAULT 0,
    solution_score integer DEFAULT 0,
    noise_score integer DEFAULT 0,
    final_score integer DEFAULT 0,
    quality_tier character varying(20) DEFAULT 'IGNORE'::character varying NOT NULL,
    discard_reason text,
    refined_at timestamp with time zone DEFAULT now(),
    validation_score integer DEFAULT 0,
    evidence_level character varying(30) DEFAULT 'NONE'::character varying,
    hard_block_reason text,
    is_blocked_gold boolean DEFAULT false,
    evidence_data jsonb DEFAULT '{}'::jsonb
);


--
-- Name: current_open_tickets; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.current_open_tickets AS
 SELECT r.id,
    r.tenant_id,
    r.raw_ticket_id,
    r.external_id,
    r.subject,
    r.service_full,
    r.service_first_level,
    r.category,
    r.urgency,
    r.status,
    r.base_status,
    r.client_business_name,
    r.client_organization_name,
    r.client_email,
    r.created_date,
    r.resolved_date,
    r.closed_date,
    r.total_actions,
    r.agent_actions_count,
    r.client_actions_count,
    r.problem_statement,
    r.technical_narrative,
    r.solution_statement,
    r.detected_modules,
    r.detected_error_codes,
    r.technical_score,
    r.solution_score,
    r.noise_score,
    r.final_score,
    r.quality_tier,
    r.discard_reason,
    r.refined_at,
    r.validation_score,
    r.evidence_level,
    r.hard_block_reason,
    r.is_blocked_gold,
    r.evidence_data,
    run.completed_at AS current_as_of
   FROM ((public.movidesk_sync_runs run
     JOIN public.movidesk_open_snapshot snapshot ON ((snapshot.run_id = run.id)))
     JOIN public.refined_tickets r ON (((r.tenant_id = snapshot.tenant_id) AND (r.external_id = snapshot.external_id))))
  WHERE ((run.id = ( SELECT movidesk_sync_runs.id
           FROM public.movidesk_sync_runs
          WHERE (movidesk_sync_runs.status = 'completed'::text)
          ORDER BY movidesk_sync_runs.completed_at DESC, movidesk_sync_runs.id DESC
         LIMIT 1)) AND ((r.base_status)::text <> ALL ((ARRAY['Resolved'::character varying, 'Closed'::character varying, 'Canceled'::character varying])::text[])));


--
-- Name: ingestion_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_log (
    id bigint NOT NULL,
    connector_id uuid,
    endpoint character varying(50),
    page_skip integer,
    tickets_fetched integer DEFAULT 0,
    status character varying(20) NOT NULL,
    http_status integer,
    error_message text,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE ingestion_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.ingestion_log IS 'Log de auditoria de cada requisição à API do Movidesk';


--
-- Name: ingestion_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ingestion_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ingestion_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ingestion_log_id_seq OWNED BY public.ingestion_log.id;


--
-- Name: knowledge_curation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_curation (
    node_id bigint NOT NULL,
    module_key text NOT NULL,
    status text NOT NULL,
    title_override text,
    problem_override text,
    solution_override text,
    version_note text,
    change_reason text NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    subtopic_key text DEFAULT 'general'::text NOT NULL,
    prerequisites text,
    expected_result text,
    keywords text[] DEFAULT '{}'::text[] NOT NULL,
    image_annotations jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT knowledge_curation_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'approved'::text, 'excluded'::text])))
);


--
-- Name: knowledge_curation_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_curation_history (
    id bigint NOT NULL,
    node_id bigint NOT NULL,
    revision integer NOT NULL,
    old_value jsonb,
    new_value jsonb NOT NULL,
    change_reason text NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: knowledge_curation_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.knowledge_curation_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: knowledge_curation_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.knowledge_curation_history_id_seq OWNED BY public.knowledge_curation_history.id;


--
-- Name: master_environment_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.master_environment_snapshots (
    id uuid NOT NULL,
    environment_id uuid NOT NULL,
    request_key text NOT NULL,
    payload_hash character(64) NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    sanitized_payload jsonb NOT NULL,
    changed_fields jsonb DEFAULT '[]'::jsonb NOT NULL,
    promoted boolean DEFAULT false NOT NULL
);


--
-- Name: COLUMN master_environment_snapshots.sanitized_payload; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.master_environment_snapshots.sanitized_payload IS 'Allowlisted diagnostic configuration. Raw INI, credentials and unknown values must never be persisted.';


--
-- Name: COLUMN master_environment_snapshots.changed_fields; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.master_environment_snapshots.changed_fields IS 'Comparison against the active snapshot before ingestion; a late report does not rewrite current state.';


--
-- Name: master_environments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.master_environments (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    installation_key text NOT NULL,
    company_key text NOT NULL,
    latest_snapshot_id uuid,
    latest_observed_at timestamp with time zone,
    last_received_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: movidesk_sync_runs_id_seq1; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.movidesk_sync_runs_id_seq1
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: movidesk_sync_runs_id_seq1; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.movidesk_sync_runs_id_seq1 OWNED BY public.movidesk_sync_runs.id;


--
-- Name: movidesk_sync_unavailable; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.movidesk_sync_unavailable (
    run_id bigint NOT NULL,
    tenant_id uuid NOT NULL,
    external_id integer NOT NULL,
    reason text NOT NULL,
    checked_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: raw_kb_articles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_kb_articles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    external_id integer NOT NULL,
    title text,
    summary text,
    categories jsonb,
    tags jsonb,
    content_html text,
    content_text text,
    payload jsonb NOT NULL,
    created_at_source timestamp with time zone,
    updated_at_source timestamp with time zone,
    ingested_at timestamp with time zone DEFAULT now()
);


--
-- Name: raw_persons; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_persons (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    external_id character varying(100) NOT NULL,
    business_name character varying(255),
    cpf_cnpj character varying(50),
    person_type integer,
    profile_type integer,
    email character varying(255),
    phone character varying(100),
    payload jsonb NOT NULL,
    ingested_at timestamp with time zone DEFAULT now()
);


--
-- Name: raw_services; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_services (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    external_id integer NOT NULL,
    name character varying(255),
    parent_service_id integer,
    is_visible boolean DEFAULT true,
    payload jsonb NOT NULL,
    ingested_at timestamp with time zone DEFAULT now()
);


--
-- Name: raw_surveys; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_surveys (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    external_id character varying(100) NOT NULL,
    ticket_id integer,
    client_id character varying(100),
    question_id character varying(100),
    value numeric,
    commentary text,
    payload jsonb NOT NULL,
    ingested_at timestamp with time zone DEFAULT now()
);


--
-- Name: raw_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_tickets (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    tenant_id uuid NOT NULL,
    source_provider character varying(50) DEFAULT 'movidesk'::character varying,
    external_id integer NOT NULL,
    payload jsonb NOT NULL,
    created_date timestamp with time zone,
    ingested_at timestamp with time zone DEFAULT now(),
    processing_status character varying(50) DEFAULT 'pending'::character varying,
    processed_at timestamp with time zone
);


--
-- Name: TABLE raw_tickets; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.raw_tickets IS 'Staging ELT: JSON bruto completo da API Movidesk, sem transformação';


--
-- Name: COLUMN raw_tickets.payload; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.raw_tickets.payload IS 'JSON completo do ticket incluindo actions, clients e customFieldValues';


--
-- Name: tenants; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tenants (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(100) NOT NULL,
    erp_system_name character varying(100) DEFAULT 'Master'::character varying,
    plan_tier character varying(50) DEFAULT 'pro'::character varying,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    api_key character varying(100),
    master_version character varying(50),
    movidesk_token character varying(200)
);


--
-- Name: TABLE tenants; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tenants IS 'Organizações clientes do DeskCortex (multi-tenant)';


--
-- Name: ticket_knowledge_nodes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ticket_knowledge_nodes (
    id bigint NOT NULL,
    source_type character varying(30) NOT NULL,
    source_id character varying(50) NOT NULL,
    source_external_id integer,
    title text NOT NULL,
    content_for_embedding text NOT NULL,
    problem text,
    diagnosis text,
    solution text,
    result text,
    product character varying(50) DEFAULT 'Master ERP'::character varying,
    modules text[],
    detected_error_codes text[],
    version_reference character varying(100),
    tier character varying(10) NOT NULL,
    confidence character varying(10) NOT NULL,
    technical_score integer DEFAULT 0,
    solution_score integer DEFAULT 0,
    validation_score integer DEFAULT 0,
    final_score integer DEFAULT 0,
    validation_evidence text[],
    hard_block_reason character varying(50),
    source_authority integer DEFAULT 50,
    metadata jsonb DEFAULT '{}'::jsonb,
    embedding public.vector(3072),
    embedding_generated boolean DEFAULT false,
    embedding_model character varying(50) DEFAULT 'gemini-embedding-2'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_at_source timestamp with time zone,
    media_path text,
    media_type text,
    manual_category text,
    shortcut_keys text[],
    screen_path text,
    media_metadata jsonb,
    CONSTRAINT ticket_knowledge_nodes_confidence_check CHECK (((confidence)::text = ANY ((ARRAY['HIGH'::character varying, 'MEDIUM'::character varying, 'LOW'::character varying])::text[]))),
    CONSTRAINT ticket_knowledge_nodes_source_authority_check CHECK (((source_authority >= 0) AND (source_authority <= 100))),
    CONSTRAINT ticket_knowledge_nodes_source_type_check CHECK (((source_type)::text = ANY ((ARRAY['OFFICIAL_KB'::character varying, 'VALIDATED_TICKET'::character varying, 'PROBABLE_TICKET'::character varying, 'INVESTIGATIVE'::character varying, 'MANUAL'::character varying])::text[]))),
    CONSTRAINT ticket_knowledge_nodes_tier_check CHECK (((tier)::text = ANY ((ARRAY['GOLD'::character varying, 'SILVER'::character varying, 'KB'::character varying, 'MANUAL'::character varying])::text[])))
);


--
-- Name: ticket_knowledge_nodes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ticket_knowledge_nodes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ticket_knowledge_nodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ticket_knowledge_nodes_id_seq OWNED BY public.ticket_knowledge_nodes.id;


--
-- Name: copilot_feedback_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_feedback_log ALTER COLUMN id SET DEFAULT nextval('public.copilot_feedback_log_id_seq'::regclass);


--
-- Name: ingestion_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_log ALTER COLUMN id SET DEFAULT nextval('public.ingestion_log_id_seq'::regclass);


--
-- Name: knowledge_curation_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_curation_history ALTER COLUMN id SET DEFAULT nextval('public.knowledge_curation_history_id_seq'::regclass);


--
-- Name: movidesk_sync_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_sync_runs ALTER COLUMN id SET DEFAULT nextval('public.movidesk_sync_runs_id_seq1'::regclass);


--
-- Name: ticket_knowledge_nodes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_knowledge_nodes ALTER COLUMN id SET DEFAULT nextval('public.ticket_knowledge_nodes_id_seq'::regclass);


--
-- Name: card_versions card_versions_pkey; Type: CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.card_versions
    ADD CONSTRAINT card_versions_pkey PRIMARY KEY (tenant_id, card_key, revision);


--
-- Name: cards cards_pkey; Type: CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.cards
    ADD CONSTRAINT cards_pkey PRIMARY KEY (tenant_id, card_key);


--
-- Name: reviews reviews_pkey; Type: CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.reviews
    ADD CONSTRAINT reviews_pkey PRIMARY KEY (id);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (id);


--
-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (tenant_id, source_key);


--
-- Name: connectors connectors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connectors
    ADD CONSTRAINT connectors_pkey PRIMARY KEY (id);


--
-- Name: copilot_feedback_log copilot_feedback_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_feedback_log
    ADD CONSTRAINT copilot_feedback_log_pkey PRIMARY KEY (id);


--
-- Name: ingestion_log ingestion_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_log
    ADD CONSTRAINT ingestion_log_pkey PRIMARY KEY (id);


--
-- Name: knowledge_curation_history knowledge_curation_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_curation_history
    ADD CONSTRAINT knowledge_curation_history_pkey PRIMARY KEY (id);


--
-- Name: knowledge_curation knowledge_curation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_curation
    ADD CONSTRAINT knowledge_curation_pkey PRIMARY KEY (node_id);


--
-- Name: master_environment_snapshots master_environment_snapshots_environment_id_request_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_environment_snapshots
    ADD CONSTRAINT master_environment_snapshots_environment_id_request_key_key UNIQUE (environment_id, request_key);


--
-- Name: master_environment_snapshots master_environment_snapshots_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_environment_snapshots
    ADD CONSTRAINT master_environment_snapshots_pkey PRIMARY KEY (id);


--
-- Name: master_environments master_environments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_environments
    ADD CONSTRAINT master_environments_pkey PRIMARY KEY (id);


--
-- Name: master_environments master_environments_tenant_id_installation_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_environments
    ADD CONSTRAINT master_environments_tenant_id_installation_key_key UNIQUE (tenant_id, installation_key);


--
-- Name: movidesk_open_snapshot movidesk_open_snapshot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_open_snapshot
    ADD CONSTRAINT movidesk_open_snapshot_pkey PRIMARY KEY (run_id, external_id);


--
-- Name: movidesk_sync_runs movidesk_sync_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_sync_runs
    ADD CONSTRAINT movidesk_sync_runs_pkey PRIMARY KEY (id);


--
-- Name: movidesk_sync_unavailable movidesk_sync_unavailable_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_sync_unavailable
    ADD CONSTRAINT movidesk_sync_unavailable_pkey PRIMARY KEY (run_id, external_id);


--
-- Name: raw_kb_articles raw_kb_articles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_kb_articles
    ADD CONSTRAINT raw_kb_articles_pkey PRIMARY KEY (id);


--
-- Name: raw_persons raw_persons_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_persons
    ADD CONSTRAINT raw_persons_pkey PRIMARY KEY (id);


--
-- Name: raw_services raw_services_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_services
    ADD CONSTRAINT raw_services_pkey PRIMARY KEY (id);


--
-- Name: raw_surveys raw_surveys_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_surveys
    ADD CONSTRAINT raw_surveys_pkey PRIMARY KEY (id);


--
-- Name: raw_tickets raw_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_tickets
    ADD CONSTRAINT raw_tickets_pkey PRIMARY KEY (id);


--
-- Name: refined_tickets refined_tickets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refined_tickets
    ADD CONSTRAINT refined_tickets_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_api_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_api_key_key UNIQUE (api_key);


--
-- Name: tenants tenants_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);


--
-- Name: tenants tenants_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tenants
    ADD CONSTRAINT tenants_slug_key UNIQUE (slug);


--
-- Name: ticket_knowledge_nodes ticket_knowledge_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_knowledge_nodes
    ADD CONSTRAINT ticket_knowledge_nodes_pkey PRIMARY KEY (id);


--
-- Name: ticket_knowledge_nodes uk_knowledge_nodes_source_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ticket_knowledge_nodes
    ADD CONSTRAINT uk_knowledge_nodes_source_id UNIQUE (source_id);


--
-- Name: raw_kb_articles uq_tenant_kb_article; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_kb_articles
    ADD CONSTRAINT uq_tenant_kb_article UNIQUE (tenant_id, external_id);


--
-- Name: raw_persons uq_tenant_person; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_persons
    ADD CONSTRAINT uq_tenant_person UNIQUE (tenant_id, external_id);


--
-- Name: raw_tickets uq_tenant_provider_external; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_tickets
    ADD CONSTRAINT uq_tenant_provider_external UNIQUE (tenant_id, source_provider, external_id);


--
-- Name: refined_tickets uq_tenant_refined_external; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refined_tickets
    ADD CONSTRAINT uq_tenant_refined_external UNIQUE (tenant_id, external_id);


--
-- Name: raw_services uq_tenant_service; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_services
    ADD CONSTRAINT uq_tenant_service UNIQUE (tenant_id, external_id);


--
-- Name: raw_surveys uq_tenant_survey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_surveys
    ADD CONSTRAINT uq_tenant_survey UNIQUE (tenant_id, external_id);


--
-- Name: curation_card_status; Type: INDEX; Schema: knowledge_curation; Owner: -
--

CREATE INDEX curation_card_status ON knowledge_curation.cards USING btree (tenant_id, status);


--
-- Name: idx_connectors_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_connectors_tenant ON public.connectors USING btree (tenant_id, provider);


--
-- Name: idx_feedback_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feedback_created_at ON public.copilot_feedback_log USING btree (created_at DESC);


--
-- Name: idx_feedback_node_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feedback_node_id ON public.copilot_feedback_log USING btree (node_id);


--
-- Name: idx_feedback_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_feedback_type ON public.copilot_feedback_log USING btree (feedback_type);


--
-- Name: idx_ingestion_log_connector; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ingestion_log_connector ON public.ingestion_log USING btree (connector_id, created_at DESC);


--
-- Name: idx_kn_authority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_authority ON public.ticket_knowledge_nodes USING btree (source_authority DESC);


--
-- Name: idx_kn_created_at_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_created_at_source ON public.ticket_knowledge_nodes USING btree (created_at_source DESC NULLS LAST);


--
-- Name: idx_kn_embed_flag; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_embed_flag ON public.ticket_knowledge_nodes USING btree (embedding_generated);


--
-- Name: idx_kn_fts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_fts ON public.ticket_knowledge_nodes USING gin (to_tsvector('portuguese'::regconfig, ((((COALESCE(title, ''::text) || ' '::text) || COALESCE(problem, ''::text)) || ' '::text) || COALESCE(solution, ''::text))));


--
-- Name: idx_kn_source_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_source_type ON public.ticket_knowledge_nodes USING btree (source_type);


--
-- Name: idx_kn_tier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_tier ON public.ticket_knowledge_nodes USING btree (tier);


--
-- Name: idx_kn_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_kn_trgm ON public.ticket_knowledge_nodes USING gin ((((COALESCE(title, ''::text) || ' '::text) || COALESCE(content_for_embedding, ''::text))) public.gin_trgm_ops);


--
-- Name: idx_master_environments_received; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_master_environments_received ON public.master_environments USING btree (tenant_id, last_received_at DESC);


--
-- Name: idx_master_snapshots_history; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_master_snapshots_history ON public.master_environment_snapshots USING btree (environment_id, observed_at DESC, received_at DESC);


--
-- Name: idx_raw_kb_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_kb_tenant ON public.raw_kb_articles USING btree (tenant_id, external_id);


--
-- Name: idx_raw_persons_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_persons_tenant ON public.raw_persons USING btree (tenant_id, external_id);


--
-- Name: idx_raw_services_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_services_tenant ON public.raw_services USING btree (tenant_id, external_id);


--
-- Name: idx_raw_surveys_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_surveys_tenant ON public.raw_surveys USING btree (tenant_id, external_id);


--
-- Name: idx_raw_tickets_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_tickets_created ON public.raw_tickets USING btree (created_date);


--
-- Name: idx_raw_tickets_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_tickets_external ON public.raw_tickets USING btree (tenant_id, external_id);


--
-- Name: idx_raw_tickets_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_tickets_processing ON public.raw_tickets USING btree (processing_status) WHERE ((processing_status)::text = 'pending'::text);


--
-- Name: idx_raw_tickets_tenant; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_raw_tickets_tenant ON public.raw_tickets USING btree (tenant_id);


--
-- Name: idx_refined_external; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_external ON public.refined_tickets USING btree (tenant_id, external_id);


--
-- Name: idx_refined_flow_closed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_flow_closed ON public.refined_tickets USING btree (closed_date) WHERE (closed_date IS NOT NULL);


--
-- Name: idx_refined_flow_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_flow_created ON public.refined_tickets USING btree (created_date);


--
-- Name: idx_refined_flow_resolved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_flow_resolved ON public.refined_tickets USING btree (resolved_date) WHERE (resolved_date IS NOT NULL);


--
-- Name: idx_refined_modules; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_modules ON public.refined_tickets USING gin (detected_modules);


--
-- Name: idx_refined_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_score ON public.refined_tickets USING btree (final_score DESC);


--
-- Name: idx_refined_tier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_refined_tier ON public.refined_tickets USING btree (tenant_id, quality_tier);


--
-- Name: idx_tkn_category_authority; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tkn_category_authority ON public.ticket_knowledge_nodes USING btree (manual_category, source_authority DESC) WHERE ((source_type)::text = 'MANUAL'::text);


--
-- Name: idx_tkn_manual_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tkn_manual_category ON public.ticket_knowledge_nodes USING btree (manual_category) WHERE (manual_category IS NOT NULL);


--
-- Name: idx_tkn_media_metadata; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tkn_media_metadata ON public.ticket_knowledge_nodes USING gin (media_metadata) WHERE (media_metadata IS NOT NULL);


--
-- Name: idx_tkn_shortcut_keys; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tkn_shortcut_keys ON public.ticket_knowledge_nodes USING gin (shortcut_keys) WHERE (shortcut_keys IS NOT NULL);


--
-- Name: idx_tkn_source_manual; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_tkn_source_manual ON public.ticket_knowledge_nodes USING btree (source_type) WHERE ((source_type)::text = 'MANUAL'::text);


--
-- Name: card_versions card_versions_tenant_id_card_key_fkey; Type: FK CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.card_versions
    ADD CONSTRAINT card_versions_tenant_id_card_key_fkey FOREIGN KEY (tenant_id, card_key) REFERENCES knowledge_curation.cards(tenant_id, card_key);


--
-- Name: reviews reviews_tenant_id_card_key_revision_fkey; Type: FK CONSTRAINT; Schema: knowledge_curation; Owner: -
--

ALTER TABLE ONLY knowledge_curation.reviews
    ADD CONSTRAINT reviews_tenant_id_card_key_revision_fkey FOREIGN KEY (tenant_id, card_key, revision) REFERENCES knowledge_curation.card_versions(tenant_id, card_key, revision);


--
-- Name: connectors connectors_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connectors
    ADD CONSTRAINT connectors_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: copilot_feedback_log copilot_feedback_log_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_feedback_log
    ADD CONSTRAINT copilot_feedback_log_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.ticket_knowledge_nodes(id) ON DELETE SET NULL;


--
-- Name: copilot_feedback_log copilot_feedback_log_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.copilot_feedback_log
    ADD CONSTRAINT copilot_feedback_log_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE SET NULL;


--
-- Name: ingestion_log ingestion_log_connector_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_log
    ADD CONSTRAINT ingestion_log_connector_id_fkey FOREIGN KEY (connector_id) REFERENCES public.connectors(id);


--
-- Name: knowledge_curation knowledge_curation_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_curation
    ADD CONSTRAINT knowledge_curation_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.ticket_knowledge_nodes(id) ON DELETE CASCADE;


--
-- Name: master_environment_snapshots master_environment_snapshots_environment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_environment_snapshots
    ADD CONSTRAINT master_environment_snapshots_environment_id_fkey FOREIGN KEY (environment_id) REFERENCES public.master_environments(id);


--
-- Name: master_environments master_environments_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.master_environments
    ADD CONSTRAINT master_environments_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: movidesk_open_snapshot movidesk_open_snapshot_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_open_snapshot
    ADD CONSTRAINT movidesk_open_snapshot_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.movidesk_sync_runs(id) ON DELETE CASCADE;


--
-- Name: movidesk_open_snapshot movidesk_open_snapshot_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_open_snapshot
    ADD CONSTRAINT movidesk_open_snapshot_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: movidesk_sync_runs movidesk_sync_runs_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_sync_runs
    ADD CONSTRAINT movidesk_sync_runs_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: movidesk_sync_unavailable movidesk_sync_unavailable_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_sync_unavailable
    ADD CONSTRAINT movidesk_sync_unavailable_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.movidesk_sync_runs(id) ON DELETE CASCADE;


--
-- Name: movidesk_sync_unavailable movidesk_sync_unavailable_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.movidesk_sync_unavailable
    ADD CONSTRAINT movidesk_sync_unavailable_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);


--
-- Name: raw_kb_articles raw_kb_articles_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_kb_articles
    ADD CONSTRAINT raw_kb_articles_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: raw_persons raw_persons_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_persons
    ADD CONSTRAINT raw_persons_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: raw_services raw_services_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_services
    ADD CONSTRAINT raw_services_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: raw_surveys raw_surveys_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_surveys
    ADD CONSTRAINT raw_surveys_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: raw_tickets raw_tickets_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_tickets
    ADD CONSTRAINT raw_tickets_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- Name: refined_tickets refined_tickets_raw_ticket_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refined_tickets
    ADD CONSTRAINT refined_tickets_raw_ticket_id_fkey FOREIGN KEY (raw_ticket_id) REFERENCES public.raw_tickets(id) ON DELETE CASCADE;


--
-- Name: refined_tickets refined_tickets_tenant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refined_tickets
    ADD CONSTRAINT refined_tickets_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 2vsUoBabtJ4BZC0HDnEbZ9NkjmmwJefy4I4KPVtViYaA8lRYk3hLdLeOyUSeqmg

