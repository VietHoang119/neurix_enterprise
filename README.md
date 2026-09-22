# Neurix Enterprise

GCP foundation for Neurix Enterprise (AI Builder Cup 2026), forked from
`neurix` (backend) and `neurix-ui` (frontend). Week 1 scope is
**infrastructure only** — no ingest, share, or ask features yet.

## What this repo does this week

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
