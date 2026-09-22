-- =============================================================================
-- 0002_enterprise_extensions.sql
-- Additive Enterprise extensions on top of 0001. Nothing is added to
-- node_pool (no org_id, no origin_node_id, no origin_user_id, no
-- processing_status — node_pool.status already tracks processing state).
-- share_node's `permission` CHECK constraint (view/comment/edit) is left
-- untouched — access level is a separate concern from the invite workflow
-- state and raw-file permission added below.
-- =============================================================================

-- Note: the auth.users shim now lives in 0001 (created early, before the
-- three tables that FK into it) — not repeated here.

-- Org tables
CREATE TABLE orgs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE org_members (
  org_id uuid NOT NULL REFERENCES orgs(id),
  uid uuid NOT NULL,                 -- Firebase uid, no FK (loose coupling from legacy tables)
  role text NOT NULL CHECK (role IN ('admin', 'member')),
  display_name text,
  joined_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, uid)
);

CREATE TABLE retrieval_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid,
  org_id uuid,
  question text NOT NULL,
  used_node_ids uuid[] NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- share_node: extend the grant TARGET to allow an org, not just a person.
-- This is the corrected design — no node_pool copy is created on share.
ALTER TABLE share_node ADD COLUMN org_id uuid REFERENCES orgs(id);
ALTER TABLE share_node ALTER COLUMN shared_user_id DROP NOT NULL;
ALTER TABLE share_node ADD CONSTRAINT share_node_target_xor
  CHECK ((shared_user_id IS NOT NULL) <> (org_id IS NOT NULL));

-- CORRECTED (previous draft of this migration wrongly crammed invite-workflow
-- state, access level, and raw-file permission all into the single
-- `permission` column). Split into three independent concerns instead:
--
--   permission   -> access LEVEL only, unchanged from core (view/comment/edit)
--                   — do NOT alter its existing CHECK constraint, leave as-is
--   status       -> invite WORKFLOW state (new column, person-share only)
--   can_view_raw -> raw-file access (new column, independent toggle)
--
-- Flow: on invite, insert with status='pending', is_active=false, permission='view'.
-- On accept: status='active', is_active=true (permission stays 'view' — that's
-- the access level, unaffected by the accept action). On decline: status='declined',
-- is_active stays false, permanently. Team-share (org_id set) skips the pending
-- step entirely: insert directly with status='active', is_active=true.
--
-- is_active is UNCHANGED and keeps its existing role: retrieve_candidate_nodes
-- filters on `sn.is_active = true`, so a pending invite (is_active=false)
-- correctly stays invisible to retrieval until accepted.
ALTER TABLE share_node ADD COLUMN status text NOT NULL DEFAULT 'active'
  CHECK (status IN ('pending', 'active', 'declined'));
ALTER TABLE share_node ADD COLUMN can_view_raw boolean NOT NULL DEFAULT false;

-- key_pool: allow org-scoped keys (a shared node's keys still need to resolve
-- inside the org's own key vocabulary for cross-member connection scoring)
ALTER TABLE key_pool ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE key_pool ADD COLUMN org_id uuid;
ALTER TABLE key_pool ADD CONSTRAINT key_pool_scope_xor
  CHECK ((user_id IS NOT NULL) <> (org_id IS NOT NULL));
CREATE UNIQUE INDEX key_pool_org_id_key_text_key
  ON key_pool (org_id, key_text) WHERE org_id IS NOT NULL;

-- connection: allow org-scoped connections (formed inside a team's view of the graph)
ALTER TABLE connection ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE connection ADD COLUMN org_id uuid;
ALTER TABLE connection ADD CONSTRAINT connection_scope_xor
  CHECK ((user_id IS NOT NULL) <> (org_id IS NOT NULL));
CREATE INDEX idx_conn_org_src ON connection (org_id, source_node) WHERE org_id IS NOT NULL;
CREATE INDEX idx_conn_org_tgt ON connection (org_id, target_node) WHERE org_id IS NOT NULL;

-- notifications: generic payload + org scope, additive to the existing message-based table
ALTER TABLE notifications ADD COLUMN org_id uuid;
ALTER TABLE notifications ADD COLUMN payload jsonb NOT NULL DEFAULT '{}';
