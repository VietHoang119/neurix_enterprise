-- =============================================================================
-- 0004_share_workflow_indexes.sql
-- Week 3 (team, share, insights). Indexes only — no new column anywhere:
-- share_node.metadata carries notify_scope/notify_targets and
-- node_pool.metadata carries raw_storage_path.
-- =============================================================================

-- A node can be offered to the same person only once at a time: at most one
-- pending-or-active person share per (node, recipient). A declined row stays
-- declined forever and does not block a fresh invite.
CREATE UNIQUE INDEX share_node_person_open_uq
  ON share_node (node_id, shared_user_id)
  WHERE shared_user_id IS NOT NULL AND status IN ('pending', 'active');

-- A node is in a team space at most once.
CREATE UNIQUE INDEX share_node_org_uq
  ON share_node (node_id, org_id)
  WHERE org_id IS NOT NULL;

CREATE INDEX idx_share_node_recipient ON share_node (shared_user_id) WHERE shared_user_id IS NOT NULL;
CREATE INDEX idx_share_node_org ON share_node (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX idx_conn_share_node ON connection (share_node_id) WHERE share_node_id IS NOT NULL;
CREATE INDEX idx_org_members_uid ON org_members (uid);
CREATE INDEX idx_notifications_unread ON notifications (user_id) WHERE is_read = false;
