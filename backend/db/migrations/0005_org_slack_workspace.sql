-- =============================================================================
-- 0005_org_slack_workspace.sql
-- Web UI polish: each org links to the Slack workspace its Neurix app lives
-- in, so the web "Open Neurix Workspace" button goes to that org's own
-- workspace instead of a hardcoded URL. Additive only — no existing column
-- is touched.
--
--   slack_team_id       -> Slack's workspace id (T...), stable across renames
--   slack_workspace_url -> where the web button links to
--
-- Filled in by the Slack OAuth install callback (routers/slack.py,
-- /slack/oauth/callback) for the installer's org, and by any /neurix slash
-- command from a member of an org that has no workspace yet (covers orgs
-- created before this migration and workspaces installed from the Slack app
-- dashboard, which never hits our callback). Both stay NULL until then; the
-- web button is disabled while they are.
-- =============================================================================

ALTER TABLE orgs ADD COLUMN slack_team_id text;
ALTER TABLE orgs ADD COLUMN slack_workspace_url text;
