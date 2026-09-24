# Neurix Enterprise

GCP foundation for Neurix Enterprise (AI Builder Cup 2026), forked from
`neurix` (backend) and `neurix-ui` (frontend). Week 1 was infrastructure
only; Week 2 adds Slack ingest + ask and a minimal web shell (personal
scope only — no org/team/share features yet).

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
