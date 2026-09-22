# GCP + Firebase Deployment Runbook

Reference for redeploying Neurix Enterprise to a new GCP project. Written from
the actual commands used to stand up `neurix-enterprise`, including the
gotchas hit along the way. Replace project/region/instance names as needed
for a new deployment.

Variables used throughout (substitute your own):
- `PROJECT_ID` = `neurix-enterprise`
- `REGION` = `asia-southeast1`
- `SQL_INSTANCE` = `neurix-enterprise-db`
- `DB_NAME` = `neurix`
- `DB_APP_USER` = `neurix_app`

---

## 0. Local prerequisites (macOS)

```bash
brew install --cask google-cloud-sdk
brew install libpq
brew link --force libpq
brew install cloud-sql-proxy
```

Node/npm needed for Firebase CLI. If `npm install -g firebase-tools` fails
with `EACCES` (common when Node was installed via Homebrew cask, not
user-owned), skip global install and use `npx firebase-tools <cmd>` for
every Firebase command instead — no permission fix needed.

```bash
node --version && npm --version
npx firebase-tools --version
```

## 1. Auth

```bash
gcloud auth login
gcloud config set project PROJECT_ID
gcloud auth application-default login   # separate from `gcloud auth login` — cloud-sql-proxy needs this specifically
npx firebase-tools login
```

## 2. Cloud SQL instance (prereq — assumed already created)

```bash
gcloud sql instances create SQL_INSTANCE \
  --database-version=POSTGRES_15 \
  --tier=db-g1-small \
  --region=REGION \
  --root-password=<chosen>

gcloud sql databases create DB_NAME --instance=SQL_INSTANCE
gcloud sql users create DB_APP_USER --instance=SQL_INSTANCE --password=<chosen>

gcloud sql instances describe SQL_INSTANCE --format="value(connectionName)"
# -> PROJECT_ID:REGION:SQL_INSTANCE (the "connection name")
```

## 3. Connect via Cloud SQL Auth Proxy

Two terminals — proxy stays running in the first for the rest of the DB work.

```bash
# Terminal A (leave running)
cloud-sql-proxy PROJECT_ID:REGION:SQL_INSTANCE

# Terminal B
psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=postgres"
```

Note: Cloud SQL's `postgres` user is `cloudsqlsuperuser`, not true
`rolsuper` — psql prompt shows `=>` not `=#`. That's expected, not an error;
it still has enough privilege for the extensions below.

## 4. Enable extensions (as `postgres`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
```

## 5. Run migrations (as `postgres`, in order)

Use the committed, fixed files in `backend/db/migrations/` — not any raw
handoff doc text. Quit interactive psql (`\q`) and run each with `-f`:

```bash
psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=postgres" \
  -f /path/to/repo/backend/db/migrations/0001_init_from_neurix.sql

psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=postgres" \
  -f /path/to/repo/backend/db/migrations/0002_enterprise_extensions.sql

psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=postgres" \
  -f /path/to/repo/backend/db/migrations/0003_retrieval_org_scope.sql
```

`NOTICE: ivfflat index created with little data` is expected on an empty
table — not an error.

## 6. Grant `neurix_app` privileges (as `postgres`)

```sql
GRANT USAGE ON SCHEMA public TO DB_APP_USER;
GRANT USAGE ON SCHEMA auth TO DB_APP_USER;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO DB_APP_USER;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth TO DB_APP_USER;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO DB_APP_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA auth TO DB_APP_USER;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO DB_APP_USER;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA auth
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO DB_APP_USER;

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO DB_APP_USER;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA auth
  GRANT USAGE, SELECT ON SEQUENCES TO DB_APP_USER;
```

## 7. Verify as `neurix_app` (not `postgres`)

```bash
psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=DB_APP_USER"
```
```sql
SELECT count(*) FROM node_pool;
INSERT INTO auth.users (id, email) VALUES (gen_random_uuid(), 'verify-neurix-app@test.local');
SELECT id, email FROM auth.users WHERE email = 'verify-neurix-app@test.local';
DELETE FROM auth.users WHERE email = 'verify-neurix-app@test.local';
```

## 8. Firebase project setup

**Do not** click "Create a project" and type a new name in the Firebase
Console — that creates a separate GCP project. Use the CLI, which correctly
attaches to the existing GCP project:

```bash
npx firebase-tools projects:addfirebase PROJECT_ID
```

**Known gotcha**: this fails with a bare `403 PERMISSION_DENIED` (no
`details`/`reason` field in the error body) if the account has never opened
the Firebase Console before — even with `roles/owner`. Fix: open
`https://console.firebase.google.com/` in a browser, log in with the same
account, accept the Firebase Terms of Service if prompted, then use the
Console's "Add project" flow and pick the existing GCP project from the
dropdown (not a typed name). Re-run the CLI command after — it will now
return `409 ALREADY_EXISTS`, which confirms success.

```bash
npx firebase-tools projects:list   # verify PROJECT_ID shows up as a Firebase project
```

## 9. Enable Auth (Google provider) + confirm Hosting

Google Sign-In provider must be enabled via Console (no reliable CLI for
this specific action):
`https://console.firebase.google.com/project/PROJECT_ID/authentication` →
Sign-in method → Add new provider → Google → Enable → set support email → Save.

Hosting's default site is auto-created when Firebase is added to the
project — just confirm it exists:

```bash
npx firebase-tools hosting:sites:list --project=PROJECT_ID
```

## 10. Secret Manager (DB password)

```bash
gcloud services enable secretmanager.googleapis.com --project=PROJECT_ID

printf 'Enter DB_APP_USER password: '
read -s DB_PASS
echo
printf '%s' "$DB_PASS" | gcloud secrets create neurix-app-db-password \
  --project=PROJECT_ID \
  --replication-policy="automatic" \
  --data-file=-
unset DB_PASS
```

**Gotcha**: don't use `read -s -p "prompt"` — zsh interprets `-p` as "read
from coprocess", not "show prompt text" (that's a bash-ism). Use a separate
`printf` for the prompt, then plain `read -s VARNAME`, as above. Verify the
secret isn't empty before moving on:

```bash
gcloud secrets versions access latest --secret=neurix-app-db-password --project=PROJECT_ID | wc -c
```

## 11. Deploy backend to Cloud Run

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project=PROJECT_ID

# Dedicated runtime SA (least privilege — not the default Compute SA)
gcloud iam service-accounts create neurix-backend-run \
  --project=PROJECT_ID \
  --display-name="Neurix Enterprise backend (Cloud Run)"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client"

gcloud secrets add-iam-policy-binding neurix-app-db-password \
  --project=PROJECT_ID \
  --member="serviceAccount:neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Dedicated build SA (also least privilege — see gotcha below)
gcloud iam service-accounts create neurix-cloudbuild-run \
  --project=PROJECT_ID \
  --display-name="Neurix Enterprise Cloud Build (source deploys)"

gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:neurix-cloudbuild-run@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.builder"

cd /path/to/repo/backend

gcloud run deploy neurix-backend \
  --source=. \
  --project=PROJECT_ID \
  --region=REGION \
  --service-account=neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com \
  --build-service-account=projects/PROJECT_ID/serviceAccounts/neurix-cloudbuild-run@PROJECT_ID.iam.gserviceaccount.com \
  --add-cloudsql-instances=PROJECT_ID:REGION:SQL_INSTANCE \
  --set-env-vars="MODE=production,FIREBASE_PROJECT_ID=PROJECT_ID,INSTANCE_CONNECTION_NAME=PROJECT_ID:REGION:SQL_INSTANCE,DB_USER=DB_APP_USER,DB_NAME=DB_NAME,FRONTEND_URL=https://PROJECT_ID.web.app" \
  --set-secrets="DB_PASSWORD=neurix-app-db-password:latest" \
  --allow-unauthenticated
```

**Gotcha #1**: `gcloud run deploy --source` builds using the **default
Compute Engine SA**, not the legacy Cloud Build SA — and on newer projects
this SA no longer auto-gets `Editor`, so the build fails with a GCS
`storage.objects.get` 403 on the `run-sources-...` bucket. Fix: use a
dedicated build SA with `roles/run.builder` (as above) instead of widening
the shared default Compute SA — passed via `--build-service-account`,
which requires the **full resource path**, not just the email:
`projects/PROJECT_ID/serviceAccounts/SA_EMAIL`.

**Gotcha #2**: `--allow-unauthenticated` can fail with `Setting IAM Policy
failed` if the org enforces **Domain Restricted Sharing**
(`iam.allowedPolicyMemberDomains`), which blocks granting roles to
`allUsers`. Check:

```bash
gcloud resource-manager org-policies describe iam.allowedPolicyMemberDomains --project=PROJECT_ID --effective
```

If it lists a customer ID (not `allValues: ALLOW`), this blocks public
access — a real architectural conflict if end users are Firebase Auth users
outside the org's Workspace domain (they will be, for any public-facing
product). Fix requires **Organization Policy Administrator** at the org
level (separate from project `Owner`/`resourcemanager.organizationAdmin` —
self-grant it via `organizations add-iam-policy-binding` if you're already
Org Admin), then a scoped, time-boxed override:

```bash
cat > /tmp/allow-public-run.yaml << 'EOF'
constraint: constraints/iam.allowedPolicyMemberDomains
listPolicy:
  allValues: ALLOW
EOF

gcloud resource-manager org-policies set-policy /tmp/allow-public-run.yaml --project=PROJECT_ID

# WAIT ~3-5 minutes here — org policy changes propagate to the enforcement
# layer separately from (and slower than) the policy metadata store; the
# `describe --effective` read can show the new value immediately while the
# actual permission check still uses stale cached policy.

gcloud run services add-iam-policy-binding neurix-backend \
  --region=REGION \
  --project=PROJECT_ID \
  --member=allUsers \
  --role=roles/run.invoker

# Immediately close the window — the binding persists even after this:
gcloud resource-manager org-policies delete iam.allowedPolicyMemberDomains --project=PROJECT_ID
```

Sanity check without a token (expect `401` from the *app*, not a Cloud Run
`403` — confirms the service is reachable and app-level auth is enforcing):

```bash
curl -i https://neurix-backend-<PROJECT_NUMBER>.REGION.run.app/health
```

## 12. Deploy frontend to Firebase Hosting

Register a Web App and get its SDK config (values here are safe to handle
in plaintext — Firebase Web API keys are meant to be public):

```bash
npx firebase-tools apps:create WEB "Neurix Enterprise Web" --project=PROJECT_ID
# -> App ID: 1:...:web:...

npx firebase-tools apps:sdkconfig WEB <APP_ID> --project=PROJECT_ID > /tmp/firebase-config.json
```

**Gotcha**: don't paste long secret-looking strings (like the API key)
through a chat/clipboard round-trip into a heredoc — some terminals/clipboard
managers silently redact matched patterns (replace with `•` bullets) even in
what actually gets written to disk, corrupting the file without any error.
Generate config files locally instead and parse them with a script so the
raw value is never displayed/copy-pasted:

```bash
cd /path/to/repo/frontend

python3 << 'EOF'
import json
with open('/tmp/firebase-config.json') as f:
    text = f.read()
cfg = json.loads(text[text.index('{'):])
with open('.env', 'w') as f:
    f.write(f"""VITE_FIREBASE_API_KEY={cfg['apiKey']}
VITE_FIREBASE_AUTH_DOMAIN={cfg['authDomain']}
VITE_FIREBASE_PROJECT_ID={cfg['projectId']}
VITE_FIREBASE_APP_ID={cfg['appId']}
VITE_API_BASE_URL=https://neurix-backend-<PROJECT_NUMBER>.REGION.run.app
""")
print("API key length:", len(cfg['apiKey']))  # sanity check: should print 39
EOF
```

Update `.firebaserc` to point at the real project (it ships with a
placeholder):

```json
{ "projects": { "default": "PROJECT_ID" } }
```

Build and deploy:

```bash
npm install
npm run build
npx firebase-tools deploy --only hosting --project=PROJECT_ID
```

## 13. End-to-end verification

1. Open the Hosting URL (`https://PROJECT_ID.web.app`), sign in with Google.
2. `Shell.tsx` auto-calls `/health` on load and shows the result on-screen
   (`Backend health: ok (uid: ...)`) — this alone proves the full chain.
3. To double-check with a manual `curl`: DevTools → Network tab → find the
   `health` request → Headers → copy the `Authorization: Bearer <token>`
   value (no code changes needed, since the app already sends this header).
   ```bash
   curl -s https://neurix-backend-<PROJECT_NUMBER>.REGION.run.app/health \
     -H "Authorization: Bearer <TOKEN>" | python3 -m json.tool
   ```
4. Confirm the row landed in `auth.users` (via the Cloud SQL Auth Proxy,
   as `neurix_app`):
   ```bash
   psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=DB_APP_USER" \
     -c "SELECT id, email, created_at FROM auth.users WHERE id = '<uid from step 2/3>';"
   ```

---

## Post-deploy cleanup checklist

- [ ] Remove the self-granted `roles/orgpolicy.policyAdmin` binding if it
      was only needed for the Domain Restricted Sharing override in step 11:
      ```bash
      gcloud organizations remove-iam-policy-binding ORG_ID \
        --member="user:<your-email>" \
        --role="roles/orgpolicy.policyAdmin"
      ```
- [ ] Cloud SQL runs 24/7 and bills whether used or not (unlike Cloud Run,
      which scales to zero). If not testing continuously, stop it between
      sessions:
      ```bash
      gcloud sql instances patch SQL_INSTANCE --activation-policy=NEVER --project=PROJECT_ID
      # ... and to resume:
      gcloud sql instances patch SQL_INSTANCE --activation-policy=ALWAYS --project=PROJECT_ID
      ```
- [ ] Set a Billing Budget alert scoped to this project if one doesn't
      already cover it (`gcloud billing budgets create`).
- [ ] For production scale-up later: right-size the Cloud SQL tier to real
      load, add an Artifact Registry cleanup policy for old build images,
      and revisit Committed Use Discounts once usage is stable.
