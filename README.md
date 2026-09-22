# Neurix Enterprise

GCP foundation for Neurix Enterprise (AI Builder Cup 2026), forked from
`neurix` (backend) and `neurix-ui` (frontend). Week 1 scope is
**infrastructure only** — no ingest, share, or chat features yet.

## What this repo does this week

- **Backend** (`backend/`): FastAPI on Cloud Run (plain Docker + uvicorn, no
  Lambda Web Adapter), Firebase ID token verification, Cloud SQL connection
  scaffolding. A single `GET /health` endpoint proves the chain end to end by
  requiring a valid Firebase token and echoing back the decoded `uid`.
- **Frontend** (`frontend/`): Vite + React shell with Firebase Auth
  (Google Sign-In) and four empty routed panels (Node list / Graph / Ask /
  Insights) for Week 2 to fill in.
- **Database** (`backend/db/migrations/`): `0001_init_from_neurix.sql`
  (schema-only baseline reconstructed from the neurix repo — see its header
  for caveats) + `0002_enterprise_extensions.sql` (additive org/notification
  extensions from the Week 1 handoff).

## Setup (manual GCP/Firebase steps — not automatable from this repo)

1. Create a GCP project. Enable the Cloud Run, Cloud SQL Admin, and Firebase
   APIs.
2. Create a Cloud SQL Postgres 15+ instance, then run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
   Apply `backend/db/migrations/0001_init_from_neurix.sql` and
   `0002_enterprise_extensions.sql` in order — **but diff 0001 against a real
   `pg_dump --schema-only` of neurix's production database first** (see the
   file's header comment for why).
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
