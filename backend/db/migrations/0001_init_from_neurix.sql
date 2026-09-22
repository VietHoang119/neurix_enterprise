-- =============================================================================
-- 0001_init_from_neurix.sql
-- Real Neurix production schema, ported verbatim (per the Week 1 handoff,
-- audited against the real Supabase schema export and retrieve_candidate_nodes
-- function). This is the authoritative baseline — do not reconstruct from
-- app/database/sql/schema.sql or other stale/incremental SQL in the neurix
-- repo, those do not match what production actually runs.
-- =============================================================================

-- Required extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;  -- required by retrieve_candidate_nodes' 'vi' branch, even though this competition build only queries in English

-- === TABLES (verbatim from Supabase schema export) ===

CREATE TABLE public.node_pool (
  fingerprint text,
  origin text DEFAULT 'web'::text,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  created_at timestamp with time zone DEFAULT now(),
  updated_at timestamp with time zone DEFAULT now(),
  user_id uuid NOT NULL,
  title text,
  ai_title text,
  source text,
  size_tokens integer,
  summary text,
  metadata jsonb,
  summary_vec vector(768),  -- FIX: export shows "USER-DEFINED" with no dimension, and both repos' CLAUDE.md confirm 768-dim Gemini embeddings — but the resolution matters here, not just as a note: `idx_node_vec` below is an ivfflat index, and pgvector refuses to build an ivfflat index on a dimension-less `vector` column (CREATE INDEX fails with "column does not have dimensions"), so this can't be left dimension-less if the index in this same file is to exist.
  text_search_vec tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, coalesce(summary, ''))) STORED,  -- FIX: the schema export shows this as a plain DEFAULT, but Postgres DEFAULT expressions cannot reference another column (`summary`) — that literally fails to CREATE TABLE. A generated column is the closest faithful equivalent: it re-derives automatically whenever `summary` changes, matching every read site (retrieve_candidate_nodes, app code) which always treats it as auto-derived and never writes to it directly.
  client_node_id uuid,
  status text NOT NULL DEFAULT 'DRAFT'::text CHECK (status = ANY (ARRAY['DRAFT'::text, 'PROCESSING'::text, 'READY'::text, 'FAILED'::text])),
  raw_content text,
  CONSTRAINT node_pool_pkey PRIMARY KEY (id)
);

CREATE TABLE public.key_pool (
  user_id uuid NOT NULL,
  key_text text NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  created_at timestamp with time zone DEFAULT now(),
  key_vec vector(768),  -- same fix as summary_vec above (idx_key_vec below is also ivfflat)
  CONSTRAINT key_pool_pkey PRIMARY KEY (id)
);

CREATE TABLE public.node_keys (
  node_id uuid NOT NULL,
  key_id uuid NOT NULL,
  weight real DEFAULT 1.0,
  CONSTRAINT node_keys_pkey PRIMARY KEY (node_id, key_id),
  CONSTRAINT node_keys_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.node_pool(id),
  CONSTRAINT node_keys_key_id_fkey FOREIGN KEY (key_id) REFERENCES public.key_pool(id)
);

-- FIX: moved ahead of `connection` (which FKs into it via share_node_id) —
-- the schema export listed it further down, but Postgres requires the
-- referenced table to exist before the FK is declared.
CREATE TABLE public.share_node (
  owner_user_id uuid NOT NULL,
  shared_user_id uuid NOT NULL,
  node_id uuid NOT NULL,
  expires_at timestamp with time zone,
  message text,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  permission text NOT NULL DEFAULT 'view'::text CHECK (permission = ANY (ARRAY['view'::text, 'comment'::text, 'edit'::text])),
  is_active boolean NOT NULL DEFAULT true,
  metadata jsonb DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT share_node_pkey PRIMARY KEY (id),
  CONSTRAINT shardnode_node_id_fkey FOREIGN KEY (node_id) REFERENCES public.node_pool(id)
);

CREATE TABLE public.connection (
  user_id uuid NOT NULL,
  source_node uuid,
  target_node uuid,
  score real NOT NULL,
  via_key_id uuid,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  created_at timestamp with time zone DEFAULT now(),
  share_node_id uuid,
  is_share boolean NOT NULL DEFAULT false,
  CONSTRAINT connection_pkey PRIMARY KEY (id),
  CONSTRAINT connection_source_node_fkey FOREIGN KEY (source_node) REFERENCES public.node_pool(id),
  CONSTRAINT connection_target_node_fkey FOREIGN KEY (target_node) REFERENCES public.node_pool(id),
  CONSTRAINT connection_share_node_id_fkey FOREIGN KEY (share_node_id) REFERENCES public.share_node(id),
  CONSTRAINT connection_via_key_id_fkey FOREIGN KEY (via_key_id) REFERENCES public.key_pool(id)
);

CREATE TABLE public.checkpoint_migrations (
  v integer NOT NULL,
  CONSTRAINT checkpoint_migrations_pkey PRIMARY KEY (v)
);

CREATE TABLE public.checkpoints (
  thread_id text NOT NULL,
  checkpoint_id text NOT NULL,
  parent_checkpoint_id text,
  type text,
  checkpoint jsonb NOT NULL,
  checkpoint_ns text NOT NULL DEFAULT ''::text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT checkpoints_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE public.checkpoint_blobs (
  thread_id text NOT NULL,
  channel text NOT NULL,
  version text NOT NULL,
  type text NOT NULL,
  blob bytea,
  checkpoint_ns text NOT NULL DEFAULT ''::text,
  CONSTRAINT checkpoint_blobs_pkey PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE public.checkpoint_writes (
  thread_id text NOT NULL,
  checkpoint_id text NOT NULL,
  task_id text NOT NULL,
  idx integer NOT NULL,
  channel text NOT NULL,
  type text,
  blob bytea NOT NULL,
  checkpoint_ns text NOT NULL DEFAULT ''::text,
  task_path text NOT NULL DEFAULT ''::text,
  CONSTRAINT checkpoint_writes_pkey PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE TABLE public.notifications (
  id uuid NOT NULL,
  user_id uuid NOT NULL,
  share_node_id uuid,
  message text NOT NULL,
  read_at timestamp with time zone,
  type character varying NOT NULL DEFAULT 'share'::character varying,
  is_read boolean NOT NULL DEFAULT false,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT notifications_pkey PRIMARY KEY (id),
  CONSTRAINT notifications_share_node_id_fkey FOREIGN KEY (share_node_id) REFERENCES public.share_node(id)
);

-- auth.users shim MUST be created before the three tables below, since they FK into it.
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE auth.users (
  id uuid PRIMARY KEY,
  email text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE public.ai_message_feedback (
  user_id uuid NOT NULL,
  message_id uuid NOT NULL,
  rating smallint NOT NULL CHECK (rating >= 1 AND rating <= 5),
  reason text,  -- export shows USER-DEFINED (an enum type not exported); using text here, add a CHECK later only if the exact enum values are needed
  comment text,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT ai_message_feedback_pkey PRIMARY KEY (id),
  CONSTRAINT ai_message_feedback_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

CREATE TABLE public.surveys (
  slug text NOT NULL,
  title text NOT NULL,
  description text,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  version integer NOT NULL DEFAULT 1,
  is_active boolean NOT NULL DEFAULT true,
  trigger_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  questions jsonb,
  CONSTRAINT surveys_pkey PRIMARY KEY (id)
);

CREATE TABLE public.survey_response (
  survey_id uuid NOT NULL,
  user_id uuid NOT NULL,
  nps smallint CHECK (nps >= 0 AND nps <= 10),
  csat smallint CHECK (csat >= 1 AND csat <= 5),
  comment text,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  submitted_at timestamp with time zone NOT NULL DEFAULT now(),
  answers jsonb NOT NULL DEFAULT '{}'::jsonb,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT survey_response_pkey PRIMARY KEY (id),
  CONSTRAINT survey_response_survey_id_fkey FOREIGN KEY (survey_id) REFERENCES public.surveys(id),
  CONSTRAINT survey_response_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

CREATE TABLE public.node_drafts (
  user_id uuid NOT NULL,
  client_node_id uuid NOT NULL,
  raw_content text NOT NULL,
  source text,
  preview jsonb,
  error text,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  status text NOT NULL DEFAULT 'DRAFT'::text,
  created_at timestamp with time zone DEFAULT now(),
  updated_at timestamp with time zone DEFAULT now(),
  CONSTRAINT node_drafts_pkey PRIMARY KEY (id)
);

CREATE TABLE public.telegram_link_tokens (
  token text NOT NULL,
  chat_id text NOT NULL,
  expires_at timestamp with time zone NOT NULL,
  used boolean DEFAULT false,
  CONSTRAINT telegram_link_tokens_pkey PRIMARY KEY (token)
);

CREATE TABLE public.telegram_sessions (
  chat_id text NOT NULL,
  user_id text NOT NULL,
  access_token text NOT NULL,
  refresh_token text NOT NULL,
  expires_at timestamp with time zone NOT NULL,
  created_at timestamp with time zone DEFAULT now(),
  updated_at timestamp with time zone DEFAULT now(),
  CONSTRAINT telegram_sessions_pkey PRIMARY KEY (chat_id)
);

CREATE TABLE public.user_journey (
  user_id uuid NOT NULL UNIQUE,
  completed_at timestamp with time zone,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  completed_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
  is_completed boolean NOT NULL DEFAULT false,
  created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT user_journey_pkey PRIMARY KEY (id)
);

CREATE TABLE public.mcp_api_keys (
  user_id uuid NOT NULL,
  key_hash text NOT NULL UNIQUE,
  label text NOT NULL,
  last_used_at timestamp with time zone,
  revoked_at timestamp with time zone,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  created_at timestamp with time zone DEFAULT now(),
  CONSTRAINT mcp_api_keys_pkey PRIMARY KEY (id),
  CONSTRAINT mcp_api_keys_user_id_fkey FOREIGN KEY (user_id) REFERENCES auth.users(id)
);

-- === INDEXES (confirmed via pg_indexes) ===

CREATE INDEX idx_conn_src ON public.connection USING btree (user_id, source_node);
CREATE INDEX idx_conn_tgt ON public.connection USING btree (user_id, target_node);
CREATE INDEX idx_node_user ON public.node_pool USING btree (user_id);
CREATE INDEX idx_node_pool_origin ON public.node_pool USING btree (origin);
CREATE INDEX idx_node_vec ON public.node_pool USING ivfflat (summary_vec vector_cosine_ops) WITH (lists='100');
CREATE INDEX idx_node_pool_fingerprint ON public.node_pool USING btree (fingerprint);
CREATE UNIQUE INDEX node_pool_user_client_node_id_uq ON public.node_pool USING btree (user_id, client_node_id) WHERE (client_node_id IS NOT NULL);
CREATE INDEX node_pool_user_status_created_at_idx ON public.node_pool USING btree (user_id, status, created_at DESC);
CREATE UNIQUE INDEX key_pool_user_id_key_text_key ON public.key_pool USING btree (user_id, key_text);
CREATE INDEX idx_key_user ON public.key_pool USING btree (user_id);
CREATE INDEX idx_key_vec ON public.key_pool USING ivfflat (key_vec vector_cosine_ops) WITH (lists='100');
-- (connection_pkey, node_pool_pkey, key_pool_pkey are auto-created by their PRIMARY KEY constraints above — not repeated here)

-- === RETRIEVAL FUNCTION (current version, with query_language param) ===

CREATE OR REPLACE FUNCTION public.retrieve_candidate_nodes(
  user_id uuid, query_text text, query_embedding vector,
  scope text DEFAULT 'merged'::text, match_count integer DEFAULT 30,
  query_language text DEFAULT 'en'::text
)
RETURNS TABLE(id uuid, user_id uuid, title text, ai_title text, source text, size_tokens integer, summary text, summary_vec vector, metadata jsonb, created_at timestamp with time zone, updated_at timestamp with time zone, text_search_vec tsvector, source_scope text, candidate_type text, similarity double precision, lexical_rank double precision)
LANGUAGE sql
STABLE
AS $function$
with shared_nodes as (
    select sn.node_id
    from public.share_node sn
    where sn.shared_user_id = retrieve_candidate_nodes.user_id
      and sn.is_active = true
      and (sn.expires_at is null or sn.expires_at > now())
),
scoped_nodes as (
    select
        np.id,
        np.user_id,
        case
            when np.user_id = retrieve_candidate_nodes.user_id then 'owned'
            else 'shared'
        end as source_scope
    from public.node_pool np
    where (
        retrieve_candidate_nodes.scope = 'owned'
        and np.user_id = retrieve_candidate_nodes.user_id
    ) or (
        retrieve_candidate_nodes.scope = 'shared'
        and np.id in (select node_id from shared_nodes)
    ) or (
        retrieve_candidate_nodes.scope = 'merged'
        and (
            np.user_id = retrieve_candidate_nodes.user_id
            or np.id in (select node_id from shared_nodes)
        )
    )
),
semantic_candidates as (
    select
        np.*,
        sc.source_scope,
        'semantic_node'::text as candidate_type,
        (1 - (np.summary_vec <=> retrieve_candidate_nodes.query_embedding))::double precision as similarity,
        null::double precision as lexical_rank
    from public.node_pool np
    join scoped_nodes sc on sc.id = np.id
    where np.summary_vec is not null
    order by np.summary_vec <=> retrieve_candidate_nodes.query_embedding asc
    limit least(retrieve_candidate_nodes.match_count, 200)
),
lexical_candidates as (
    select
        np.*,
        sc.source_scope,
        'lexical_node'::text as candidate_type,
        null::double precision as similarity,
        case
            when retrieve_candidate_nodes.query_language = 'vi' then
                ts_rank_cd(
                    to_tsvector('simple', unaccent(coalesce(np.title, '') || ' ' || coalesce(np.summary, ''))),
                    websearch_to_tsquery('simple', unaccent(retrieve_candidate_nodes.query_text))
                )
            else
                ts_rank_cd(
                    np.text_search_vec,
                    websearch_to_tsquery('english', retrieve_candidate_nodes.query_text)
                )
        end::double precision as lexical_rank
    from public.node_pool np
    join scoped_nodes sc on sc.id = np.id
    where
        case
            when retrieve_candidate_nodes.query_language = 'vi' then
                to_tsvector('simple', unaccent(coalesce(np.title, '') || ' ' || coalesce(np.summary, '')))
                    @@ websearch_to_tsquery('simple', unaccent(retrieve_candidate_nodes.query_text))
            else
                np.text_search_vec @@ websearch_to_tsquery('english', retrieve_candidate_nodes.query_text)
        end
    order by lexical_rank desc
    limit least(retrieve_candidate_nodes.match_count, 200)
),
key_candidates as (
    select
        np.*,
        sc.source_scope,
        'key_node'::text as candidate_type,
        (1 - (kp.key_vec <=> retrieve_candidate_nodes.query_embedding))::double precision as similarity,
        null::double precision as lexical_rank
    from public.key_pool kp
    join public.node_keys nk on nk.key_id = kp.id
    join scoped_nodes sc on sc.id = nk.node_id
    join public.node_pool np on np.id = sc.id
    where kp.key_vec is not null
    order by kp.key_vec <=> retrieve_candidate_nodes.query_embedding asc
    limit least(retrieve_candidate_nodes.match_count, 200)
),
all_candidates as (
    select * from semantic_candidates
    union all
    select * from lexical_candidates
    union all
    select * from key_candidates
)
select distinct on (id)
    id, user_id, title, ai_title, source, size_tokens, summary, summary_vec,
    metadata, created_at, updated_at, text_search_vec, source_scope,
    candidate_type, similarity, lexical_rank
from all_candidates
order by id, similarity desc nulls last, lexical_rank desc nulls last
limit least(retrieve_candidate_nodes.match_count, 200);
$function$;

-- Deliberately excluded (superseded or unrelated — do not port):
--   hybrid_search_node_pool, hybrid_search_sample, full_text_search_node_pool
--   (older/experimental, replaced by retrieve_candidate_nodes above); all
--   storage.* functions (storage.search, storage.search_v2,
--   storage.search_by_timestamp) — Supabase Storage internals, unrelated to
--   Neurix's own schema, and Cloud SQL has no storage.objects table anyway.
