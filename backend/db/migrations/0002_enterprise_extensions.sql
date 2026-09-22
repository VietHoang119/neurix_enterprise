-- =============================================================================
-- 0002_enterprise_extensions.sql
-- Additive Enterprise extensions on top of 0001. No column on node_pool,
-- key_pool, connection, node_drafts, or share_node is dropped or renamed.
--
-- DEVIATION FROM THE HANDOFF — read before applying:
-- The handoff lists `notifications` under "New tables", but that table
-- already exists in neurix today (see 0001) with columns
-- (id, user_id, type, message, is_read, created_at) — no `payload jsonb`,
-- no `org_id`. Recreating it would collide with the existing table, so this
-- migration ALTERs it additively instead (adds org_id + payload, keeps the
-- existing `message` column rather than dropping it). Confirm this matches
-- what you actually see on the real database before running.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- node_pool extensions
-- ---------------------------------------------------------------------------
ALTER TABLE node_pool ADD COLUMN raw_storage_path text;
ALTER TABLE node_pool ADD COLUMN processing_status text NOT NULL DEFAULT 'processing';
ALTER TABLE node_pool ADD COLUMN org_id uuid;
ALTER TABLE node_pool ADD COLUMN origin_node_id uuid;
ALTER TABLE node_pool ADD COLUMN origin_user_id uuid;

CREATE INDEX idx_node_pool_org_id ON node_pool(org_id);

-- ---------------------------------------------------------------------------
-- key_pool extensions
-- ---------------------------------------------------------------------------
ALTER TABLE key_pool ADD COLUMN org_id uuid;

ALTER TABLE key_pool ADD CONSTRAINT key_pool_scope_xor
  CHECK ((user_id IS NOT NULL) <> (org_id IS NOT NULL));

CREATE INDEX idx_key_pool_org_id ON key_pool(org_id);

-- ---------------------------------------------------------------------------
-- share_node: `permission` is a plain `text` column (see 0001 / share_node_repo.py),
-- not an enum type, so no DDL is needed to allow new values. Application code
-- is responsible for accepting 'pending' | 'accepted' | 'declined' | 'view_raw'
-- alongside the existing 'view' / 'comment' / 'edit' values — out of scope
-- for this infra-only migration.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- notifications: additive extension of the existing table (see deviation
-- note above) instead of CREATE TABLE.
-- ---------------------------------------------------------------------------
ALTER TABLE notifications ADD COLUMN org_id uuid;
ALTER TABLE notifications ADD COLUMN payload jsonb NOT NULL DEFAULT '{}';

CREATE INDEX idx_notifications_org_id ON notifications(org_id);

-- ---------------------------------------------------------------------------
-- New tables
-- ---------------------------------------------------------------------------
CREATE TABLE orgs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_by uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE org_members (
  org_id uuid NOT NULL REFERENCES orgs(id),
  uid uuid NOT NULL,
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

-- NOTE: if `\dt` on the target database shows a pre-existing `users` table
-- (SERIAL id, `name` column — from app/database/sql/schema.sql) that is
-- confirmed unused in production, drop it first:
--   DROP TABLE IF EXISTS users;
-- Not done automatically here — its production usage could not be verified
-- without direct database access (see 0001's header note).
CREATE TABLE users (
  uid uuid PRIMARY KEY,            -- Firebase uid
  email text NOT NULL,
  display_name text,
  created_at timestamptz NOT NULL DEFAULT now()
);
