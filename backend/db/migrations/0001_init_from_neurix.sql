-- =============================================================================
-- 0001_init_from_neurix.sql
-- Schema-only baseline, reconstructed from the neurix repo (table structures
-- only, no data, no `auth.users` FK — Supabase-specific bits dropped).
--
-- IMPORTANT — how this was produced, and what to verify before relying on it:
-- neurix has no single checked-in schema dump. Its schema lives in a live
-- Supabase project. This file was reconstructed by reading:
--   - app/database/sql/schema.sql (stale — SERIAL ids, VECTOR(1536); does not
--     match the uuid-keyed tables the app actually queries, so NOT used here)
--   - sql/*.sql (incremental ALTERs: node_drafts, fingerprint/origin columns,
--     unaccent extension)
--   - column names referenced in app/repositories/*.py and sql/retrieve_candidate_nodes.sql
-- No live database access was available to cross-check this against a real
-- `pg_dump --schema-only`. Before applying to Cloud SQL, diff this against an
-- actual dump of the neurix production schema and fix any mismatch — treat
-- column types/defaults/nullability here as best-effort, not verified fact.
--
-- Notably NOT ported here (out of scope for infra-only Week 1):
--   - RPC functions `get_next_survey()` / `retrieve_candidate_nodes()` — these
--     are query logic tied to Supabase's `auth.users` and to un-org-aware
--     scoping; they need a rewrite once share/retrieval features are ported,
--     not a verbatim copy.
--   - The legacy `users` table from app/database/sql/schema.sql (SERIAL id,
--     `name` column) — every other table's `user_id` is uuid and referenced
--     Supabase's `auth.users`, so this table looks like dead scaffolding.
--     If `\dt` on the real neurix database shows it in active use, add it
--     here before proceeding.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- ---------------------------------------------------------------------------
-- node_pool
-- ---------------------------------------------------------------------------
CREATE TABLE node_pool (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    title text,
    ai_title text,
    source text,
    size_tokens integer,
    summary text NOT NULL,
    summary_vec vector(768),
    metadata jsonb NOT NULL DEFAULT '{}',
    text_search_vec tsvector,
    fingerprint text,
    origin text DEFAULT 'web',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_node_pool_user_id ON node_pool(user_id);
CREATE INDEX idx_node_pool_created_at ON node_pool(created_at);
CREATE INDEX idx_node_pool_fingerprint ON node_pool(fingerprint);
CREATE INDEX idx_node_pool_origin ON node_pool(origin);
CREATE INDEX idx_node_pool_text_search_vec ON node_pool USING gin(text_search_vec);
CREATE INDEX idx_node_pool_summary_vec ON node_pool USING ivfflat (summary_vec vector_cosine_ops) WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- key_pool
-- ---------------------------------------------------------------------------
CREATE TABLE key_pool (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid,
    key_text text NOT NULL,
    key_vec vector(768),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_key_pool_user_id ON key_pool(user_id);
CREATE INDEX idx_key_pool_key_text ON key_pool(key_text);

-- ---------------------------------------------------------------------------
-- node_keys (node_pool <-> key_pool)
-- ---------------------------------------------------------------------------
CREATE TABLE node_keys (
    node_id uuid NOT NULL REFERENCES node_pool(id) ON DELETE CASCADE,
    key_id uuid NOT NULL REFERENCES key_pool(id) ON DELETE CASCADE,
    weight float NOT NULL DEFAULT 1.0,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, key_id)
);

-- ---------------------------------------------------------------------------
-- connection (singular — matches app/repositories/connection_repo.py; the
-- handoff doc calls it "connections", but that name does not exist in the
-- actual repo. Renaming it would violate "additive only", so the real name
-- is kept here.)
-- ---------------------------------------------------------------------------
CREATE TABLE connection (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    source_node uuid NOT NULL REFERENCES node_pool(id) ON DELETE CASCADE,
    target_node uuid NOT NULL REFERENCES node_pool(id) ON DELETE CASCADE,
    score float NOT NULL DEFAULT 0.0,
    via_key_id uuid REFERENCES key_pool(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_connection_source_node ON connection(source_node);
CREATE INDEX idx_connection_target_node ON connection(target_node);
CREATE INDEX idx_connection_user_id ON connection(user_id);

-- ---------------------------------------------------------------------------
-- node_drafts (verbatim from sql/20240920_add_node_drafts.sql)
-- ---------------------------------------------------------------------------
CREATE TABLE node_drafts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    client_node_id uuid NOT NULL,
    raw_content text NOT NULL,
    source text,
    status text NOT NULL DEFAULT 'DRAFT',
    preview jsonb,
    error text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX node_drafts_user_client_uniq ON node_drafts(user_id, client_node_id);

-- ---------------------------------------------------------------------------
-- share_node
-- ---------------------------------------------------------------------------
CREATE TABLE share_node (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id uuid NOT NULL,
    shared_user_id uuid NOT NULL,
    node_id uuid NOT NULL REFERENCES node_pool(id) ON DELETE CASCADE,
    permission text NOT NULL,
    message text,
    is_active boolean NOT NULL DEFAULT true,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_share_node_shared_user_id ON share_node(shared_user_id);
CREATE INDEX idx_share_node_node_id ON share_node(node_id);

-- ---------------------------------------------------------------------------
-- notifications (already exists in neurix today — kept here, extended
-- additively in 0002 rather than recreated; see the note there)
-- ---------------------------------------------------------------------------
CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    type text NOT NULL,
    message text,
    is_read boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_notifications_user_id ON notifications(user_id);

-- ---------------------------------------------------------------------------
-- ai_message_feedback (matches app/repositories/feedback_repository.py; the
-- handoff's Environment table refers to it loosely as "feedback")
-- ---------------------------------------------------------------------------
CREATE TABLE ai_message_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL,
    rating integer,
    comment text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_ai_message_feedback_user_id ON ai_message_feedback(user_id);

-- ---------------------------------------------------------------------------
-- surveys / survey_response
-- ---------------------------------------------------------------------------
CREATE TABLE surveys (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title text NOT NULL,
    description text,
    questions jsonb,
    version integer NOT NULL DEFAULT 1,
    is_active boolean NOT NULL DEFAULT true,
    trigger_rule jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE survey_response (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    survey_id uuid NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
    user_id uuid NOT NULL,
    answers jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_survey_response_user_id ON survey_response(user_id);
