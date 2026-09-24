# Neurix Enterprise

GCP foundation for Neurix Enterprise (AI Builder Cup 2026), forked from
`neurix` (backend) and `neurix-ui` (frontend). Week 1 was infrastructure
only; Week 2 adds Slack ingest + ask and a minimal web shell (personal
scope only — no org/team/share features yet).

## Week 3: team, share, insights, team graph

- **Team**: `/neurix team create <name>` (caller becomes admin),
  `/neurix team invite` (admin only; a `users_select` modal — the person
  must have signed in to the web app once, or the inviter is told so),
  `/neurix team list`. One team per person; no acceptance step for
  membership.
- **Share to a person**: `/neurix share` → modal (your READY notes +
  recipient) → `share_node` row `status='pending'`, `is_active=false`, and a
  DM to the recipient with Accept / Decline. Accept → `active` (permanent);
  Decline → `declined` (permanent). Only a pending invite can be cancelled
  (button on the sender's confirmation DM).
- **Share to team**: `/neurix share-team` → modal (note + notify scope:
  admin only / everyone / specific people) → `share_node` with `org_id`,
  `status='active'` at once; the scope is kept in `share_node.metadata`
  (`notify_scope`, `notify_targets`).
- **Persisted share connections**: Accept and share-to-team run one job
  (`services/share.persist_share_connections`) that writes real
  `connection` rows (`is_share=true`, `share_node_id`, and `user_id` for a
  person share or `org_id` for a team share), using the ingest job's rule:
  one row per (other node, key), score 1.0 for the same key text or the key
  similarity at ≥ `KEYS_SIMILARITY_SCORE`. Person scope = the recipient's
  own notes; team scope = the other notes in the team space. Graphs read
  only these rows — neurix's live `_build_incoming_share_connections` is
  deliberately not ported, so a share edge can't be counted twice (see
  `test_shared_node_connections_appear_once_in_graph`).
- **Notifications**: invites and cross-member connections are immediate
  DMs (a team share DMs both owners of every new cross-member edge; an
  accepted person share DMs the sender a count — never the titles of the
  recipient's private notes). Accepted/declined, "joined your network",
  "added to team space" (per notify scope) and raw-access changes go to the
  `notifications` table and show up in `/neurix stats`, which then marks
  them read.
- **`/neurix stats`**: plain-SQL insights for you (volume, shares, orphan %,
  keys/note, bridges to other people's notes, how often your notes were
  used in answers) and your team space (the same, plus per-member
  expertise and a bus-factor flag for any key shared by ≥ 2 team notes
  that only one member contributed). Org numbers only look at notes shared
  to the team.
- **Raw files**: on every save the original (the text, or the uploaded
  file) goes to Cloud Storage at `raw/{uid}/{timestamp}-{name}`, its path in
  `node_pool.metadata.raw_storage_path` (deleted again on Discard).
  `/neurix raw-access <note> @person|team on|off` flips `can_view_raw` on an
  active share, any time. `/neurix file <note>` returns a 10-minute signed
  URL if you own the note or an active share of it to you (or your team)
  has `can_view_raw`.
- `/neurix ask` uses org scope automatically for team members (own notes,
  accepted shares, and the team space); `retrieval_log.org_id` is set.
- **Web**: a Team graph panel (`GET /team/graph`) — same static layout as
  the personal graph, nodes outlined by original owner, cross-member edges
  dashed purple. The personal graph now also shows accepted shared notes
  and their persisted edges.
- New migration `0004_share_workflow_indexes.sql` adds indexes only; no
  column on `node_pool` or `share_node`. Deployment: runbook §15.

## Week 2: Slack ingest, ask, web shell

- `/neurix save <text>` → ephemeral "⏳ Extracting..." ack → a preview card
  (title, summary, keys; ✅ Confirm / ✏️ Edit / ❌ Discard) posted in the
  user's DM with the bot. The preview is staged in `node_drafts.preview`.
- **Edit**: reply in the card's thread with new summary text and/or
  `+key: X` / `-key: Y` lines; the card is updated in place. Any number of
  rounds.
- **Confirm**: inserts `node_pool` with `status='PROCESSING'` + `node_keys`,
  then a background job embeds an edited summary, dedups new keys against
  `key_pool` (cosine >= 0.9, per user — merge on match), writes personal
  `connection` rows, and sets `status='READY'` (`'FAILED'` +
  `metadata.error` on error). The card ends as
  "✅ Saved — N new connections, key 'X' merged into 'Y'".
- **Discard**: deletes the draft.
- **Files** DM'd to the bot (PDF, images, .docx, .xlsx, .txt, .md, <= 20 MB):
  PDF/images go straight to Gemini for text; docx/xlsx via python-docx /
  openpyxl; then the same preview flow.
- `/neurix ask <question>`: personal-scope `retrieve_candidate_nodes`, ranked
  like neurix's chat agent, answered by Gemini from those nodes only with
  `[n]` citations + a "Sources:" line; logged to `retrieval_log`.
- Web shell: node list with a `⏳ Processing` badge, and a static personal
  graph (simulated once, then frozen) showing each node's top-3 connections,
  with a "Show all connections" toggle.
- Every `/slack/*` request is HMAC-verified against `SLACK_SIGNING_SECRET`
  first; anything else gets a 401.
- No new migration: everything uses existing columns.

Slack App configuration (scopes, event subscriptions) and the Cloud Run
redeploy (`--no-cpu-throttling`, new secrets) are in
`docs/DEPLOYMENT_RUNBOOK.md` §14.

Backend tests (`cd backend && pytest`) run the Slack flows against a real
Postgres + pgvector with the real migrations applied (set
`TEST_DATABASE_URL`, default
`postgresql+pg8000://postgres:postgres@localhost:5432/postgres`); Gemini and
Slack are faked. They are skipped if no database is reachable.

## Week 1: what was set up

- **Backend** (`backend/`): FastAPI on Cloud Run (plain Docker + uvicorn, no
  Lambda Web Adapter, reads `$PORT`), Firebase ID token verification, Cloud
  SQL connection via the Cloud SQL Python Connector. A single `GET /health`
  endpoint proves the chain end to end: it requires a valid Firebase token,
  upserts `{id, email}` into the `auth.users` shim, and echoes back that
  Postgres `user_id`.
- **Frontend** (`frontend/`): Vite + React shell with Firebase Auth
  (Google Sign-In) and four empty routed panels (Node list / Graph / Ask /
  Insights) for a later week to fill in.
- **Database** (`backend/db/migrations/`): `0001_init_from_neurix.sql` (the
  real Neurix schema, ported verbatim), `0002_enterprise_extensions.sql`
  (additive org/share-workflow extensions), `0003_retrieval_org_scope.sql`
  (extends `retrieve_candidate_nodes` with an optional `org_id` overload).

### uid -> user_id mapping

`auth.users.id` (and every FK into it — `ai_message_feedback`,
`survey_response`, `mcp_api_keys`) is typed `uuid`. Firebase uids are ~28
char alphanumeric strings, not valid UUIDs, so they can't be inserted as-is.
`core/auth.py` derives a deterministic `uuid5` of the Firebase uid as
`user_id` — the same Firebase uid always maps to the same Postgres id, with
no separate lookup table needed. This is what gets upserted into
`auth.users` and returned by every authenticated endpoint.

### Share model (verified against the `neurix` backend)

`app/repositories/share_node_repo.py` and `app/services/graph_service.py` in
`neurix` confirm the reference-not-copy design this schema follows: sharing
a node only ever writes a row to `share_node` — `share_nodes()` never
creates a `node_pool` copy. Incoming shares are not pre-materialized into
`node_keys`/`connection` rows either; `graph_service.get_graph_nodes_connections()`
computes "share connections" on the fly at query time
(`_build_incoming_share_connections`), by intersecting key-text overlap
between the recipient's owned nodes and their shared nodes. Enterprise
team-sharing (`share_node.org_id`) extends this same reference model — see
`0002_enterprise_extensions.sql`.

## Setup (manual GCP/Firebase steps — not automatable from this repo)

1. Create a GCP project. Enable the Cloud Run, Cloud SQL Admin, and Firebase
   APIs.
2. Create a Cloud SQL Postgres 15+ instance, then apply, in order:
   ```bash
   psql "$CONNECTION" -f backend/db/migrations/0001_init_from_neurix.sql
   psql "$CONNECTION" -f backend/db/migrations/0002_enterprise_extensions.sql
   psql "$CONNECTION" -f backend/db/migrations/0003_retrieval_org_scope.sql
   psql "$CONNECTION" -f backend/db/migrations/0004_share_workflow_indexes.sql
   ```
3. Create a Firebase project **linked to the same GCP project**. Enable
   Authentication -> Google provider, and enable Hosting.
4. Deploy the backend to Cloud Run from `backend/Dockerfile`, with the Cloud
   SQL instance attached and `FIREBASE_PROJECT_ID` / `INSTANCE_CONNECTION_NAME`
   / `DB_USER` / `DB_PASSWORD` / `DB_NAME` set as env vars (see
   `backend/.env.sample`).
5. Fill in `frontend/.env` from `frontend/.env.sample` (Firebase Web SDK
   config + the Cloud Run URL), then `npm run build` and
   `firebase deploy --only hosting` from `frontend/` (after setting the real
   project id in `.firebaserc`).

## Local development

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.sample .env   # fill in FIREBASE_PROJECT_ID at minimum
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm install
cp .env.sample .env   # fill in Firebase Web SDK config
npm run dev
```
