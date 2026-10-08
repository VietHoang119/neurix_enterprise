# GCP + Firebase Deployment Runbook

For provisioning a new tenant, see **[`deploy/README.md`](../deploy/README.md)**
and **[`deploy/TENANT_RUNBOOK.md`](../deploy/TENANT_RUNBOOK.md)** — a
semi-automated Terraform + script package built from this runbook that now
automates most of the steps below (Terraform for infrastructure, scripted
migrations/grants, `release.sh`/`rollback.sh` for ongoing code pushes) and is
the maintained, live-tested source of truth for how tenant provisioning
actually works today. This document remains the reference for what each step
does conceptually and its gotchas, and is the record of how production itself
was actually built — but wherever the two disagree on naming, tooling, or
process, **`deploy/` wins**; this document has been reconciled to match it.

**Relationship between this document and `deploy/`.** `neurix-enterprise` is
a single, hand-created production instance that predates `deploy/`'s tooling
entirely — its Cloud SQL instance, Cloud Run service, Firebase project link,
secrets, and service accounts were created by the raw `gcloud`/`firebase`
commands below, one at a time, and **no Terraform state exists for any of
it**. `deploy/`'s `terraform/platform` refuses to ever run against
`neurix-enterprise` for exactly this reason (see `deploy/README.md`'s "Hard
safety rule"). Conceptually, though, production corresponds to what
`deploy/`'s `terraform/platform` plus one `terraform/tenant` apply would
produce today — same Cloud SQL/Cloud Run/Firebase shape, same required env
vars, same migration+grant flow. Any operational convention **going
forward** (naming, required settings, auth handling, the migration process,
how a second tenant would be added) should follow `deploy/`'s conventions,
since that is the maintained, tested system — even though production's own
resources were created by hand, aren't Terraform-managed, and (per the naming
note below) don't literally match `deploy/`'s tenant-suffixed names.

Reference for redeploying Neurix Enterprise to a new GCP project, and for
manual `gcloud`/`firebase` operations against the existing production
project. Written from the actual commands used to stand up
`neurix-enterprise`, including the gotchas hit along the way, and updated
with lessons from `deploy/`'s live tenant-provisioning tests that apply
equally to hand-run commands against production. Replace project/region/
instance names as needed for a new deployment.

Variables used throughout (substitute your own):
- `PROJECT_ID` = `neurix-enterprise`
- `REGION` = `asia-southeast1`
- `SQL_INSTANCE` = `neurix-enterprise-db`
- `DB_NAME` = `neurix`
- `DB_APP_USER` = `neurix_app`
- `TENANT_ID` = a short slug identifying this deployment (e.g. `acme`) — see
  step 11

**Naming convention note.** The names above (`neurix-enterprise-db`,
`neurix`, `neurix_app`, `neurix-backend`, `neurix-backend-run`,
`neurix-cloudbuild-run`, and the un-suffixed secret names in step 10/14) are
production's actual, real names — they predate `deploy/`'s tooling and are
**not** tenant-suffixed, because production has only ever had one tenant.
`deploy/`'s Terraform (`terraform/tenant/main.tf`) uses a tenant-suffixed
convention instead — `neurix_<tenant_id>` (database), `app_<tenant_id>` (DB
user), `neurix-backend-<tenant_id>` (Cloud Run service),
`neurix-<tenant_id>-run` (runtime service account), and
`db-password-<tenant_id>` / `slack-bot-token-<tenant_id>` /
`slack-signing-secret-<tenant_id>` / `gemini-api-key-<tenant_id>` (secrets),
with a shared platform-level SQL instance (`neurix-db` by default) and build
service account (`neurix-cloudbuild` by default). **Follow `deploy/`'s
tenant-suffixed convention for any new work** — a new tenant provisioned
against production (see §17) or a fresh GCP project — even though
production's own existing resources keep their original, un-suffixed names.

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

**Three separate, independently-expiring login mechanisms** — this trips
people up in practice (confirmed during `deploy/`'s live tenant-provisioning
test, `deploy/TENANT_RUNBOOK.md` step 2), and applies just as much to hand-run
`gcloud`/`firebase` commands against production as it does to Terraform:

- `gcloud auth login` — for `gcloud` commands you type directly.
- `gcloud auth application-default login` — a separate credential set (ADC),
  needed by `cloud-sql-proxy` (step 3) and by Terraform's `google` provider.
- `npx firebase-tools login` (add `--reauth` to force a fresh login) — used by
  every `firebase-tools` call: Firebase project setup (step 8), Hosting
  (step 9, step 12), Web App registration (step 12).

An auth error only tells you a command failed, not which of the three expired
— if you hit `invalid_rapt`, "Reauthentication required", or a bare
`firebase-tools` 401, work out which mechanism actually issued the failing
call before re-running the wrong `login` command.

**Before running anything with side effects, especially if you also work
against other GCP projects (a test project, `deploy/`'s throwaway projects,
etc.):**

```bash
gcloud config get-value project   # must print PROJECT_ID (neurix-enterprise), not some other project
```

A dedicated `gcloud` configuration for production avoids this mistake
entirely:

```bash
gcloud config configurations create neurix-enterprise
gcloud config set project neurix-enterprise
gcloud config set account <your-account-email>
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

`0001_init_from_neurix.sql` already contains both `CREATE EXTENSION IF NOT
EXISTS` statements, so the migration runner (§5) creates these
automatically when it applies 0001 as `postgres`. This manual step is kept
here only as an optional early sanity check (e.g. to confirm the `vector`
extension is available on the instance before running anything else) — it
is no longer strictly required as a separate step.

## 5. Run migrations — `scripts/run_migrations.py` (safe to re-run)

As of the backend hardening pass, migrations are no longer applied by hand
with `psql -f` one file at a time. `backend/scripts/run_migrations.py` reads
`backend/db/migrations/*.sql` in filename order, tracks what's already been
applied in a `schema_migrations` table, and runs only what's missing — each
migration in its own transaction. On failure it stops and names exactly
which file failed; migrations before it stay applied, so re-running after a
fix resumes from there instead of re-attempting ones already applied. It is
always safe to run, including with nothing new to apply.

Current migrations, applied in this order:

| File | What it does |
|---|---|
| `0001_init_from_neurix.sql` | Base schema ported from `neurix`, plus the `vector`/`unaccent` extensions and the `auth.users` shim |
| `0002_enterprise_extensions.sql` | Additive org/share-workflow tables and constraints |
| `0003_retrieval_org_scope.sql` | Adds an optional `org_id` overload to `retrieve_candidate_nodes` |
| `0004_share_workflow_indexes.sql` | Indexes only (one open invite per note+person, a note in a team space once) |
| `0005_org_slack_workspace.sql` | Adds `orgs.slack_team_id` / `orgs.slack_workspace_url` (nullable) |

Run it from `backend/`, pointed at the Cloud SQL instance directly (it uses
the same `db.session.get_engine()` the app itself uses — no separate
`--database-url` flag, and no need for the Cloud SQL Proxy from step 3 as
long as `gcloud auth application-default login` from step 1 is current):

```bash
cd backend
INSTANCE_CONNECTION_NAME="PROJECT_ID:REGION:SQL_INSTANCE" \
DB_USER="postgres" \
DB_PASSWORD="<postgres root password>" \
DB_NAME="DB_NAME" \
python -m scripts.run_migrations
```

**Must be run as `postgres`, not `neurix_app`** — `neurix_app` has no DDL
permission, same as every earlier manual migration in this project's
history.

**Going forward:** whenever a new migration file lands (`0006_...sql` and
up), run this exact command again before deploying the code that depends on
it. The script skips anything already recorded, so running it with nothing
new to apply is a harmless no-op — there's no need to track "have I already
run this" separately.

**`deploy/`'s tooling runs this exact same script** (`scripts/provision.sh
migrate` for a new tenant, `scripts/release.sh` for an ongoing release), then
immediately runs the grant step below automatically (`grant_tenant.py`) —
see "Grants" in `deploy/README.md`. For production, keep using the manual
command above (production isn't under `deploy/`'s tenant tooling), but be
aware that `deploy/`'s automated path is the same two steps this section and
§6 describe by hand, just scripted.

**Destructive migrations need care that this manual flow doesn't enforce.**
`deploy/`'s `release.sh` runs `scripts/check_migration_safety.py` before
building any image, refusing a migration that drops/renames/tightens/retypes
a column unless it's explicitly marked
`-- destructive-migration-approved: <reason>` — see `deploy/README.md`'s
"Expand/contract: why migrations run before the code swap" for the full
rationale (old code must keep working against the new schema during a
rolling release). Nothing in this manual flow checks for that automatically;
apply the same expand/contract discipline by hand before running a migration
against production that isn't purely additive.

Verify via the tracking table itself (through the Cloud SQL Proxy from step
3):

```bash
psql "host=127.0.0.1 port=5432 sslmode=disable dbname=DB_NAME user=postgres" \
  -c "SELECT * FROM schema_migrations ORDER BY version;"
```

Expect one row per file above. `NOTICE: ivfflat index created with little
data` during 0001 is expected on an empty table — not an error.

**Existing (pre-runner) deployments**: if a database already has 0001
applied by hand (no `schema_migrations` row for it), the runner detects this
by probing for `node_pool` and records `0001` as applied without
re-running its SQL, since 0001's `CREATE TABLE` statements are not
idempotent. 0002–0005 are all written with `IF NOT EXISTS` /
`pg_constraint` guards, so they're simply re-run as no-ops and recorded
normally.

## 6. Grant `neurix_app` privileges (as `postgres`)

This is the same `ALTER DEFAULT PRIVILEGES` technique
`deploy/scripts/grant_app_user.sql.tmpl` automates via `grant_tenant.py` for
tenant deployments (see "Grants: ending the 'forgot GRANT after migration'
failure class" in `deploy/README.md`) — the `ALTER DEFAULT PRIVILEGES FOR
ROLE postgres` statements below mean this only needs to be run **once**:
every table/sequence a *future* migration creates (as `postgres`, which the
runner always connects as) is automatically granted to `DB_APP_USER` from
then on. If this was already run once for `neurix-enterprise`, migrations
0002 onward did not need it re-run, and neither will 0006 onward.

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

This is how `neurix-enterprise`'s Cloud Run service was originally created,
by hand, and it's still the reference for what a first-time deploy involves.
**For any new tenant, this entire step is replaced by Terraform**
(`terraform/tenant/cloud_run.tf`, run via `scripts/provision.sh
tenant-resources`/`app`) — see `deploy/README.md`'s "The 5 phases". **For an
ongoing code change to production or any tenant after the first deploy, use
`scripts/release.sh`/`scripts/rollback.sh` instead of re-running
`gcloud run deploy` by hand** — see `deploy/README.md`'s "Releasing code vs
changing infrastructure", which explains why those two scripts deliberately
never call Terraform. The manual command below remains correct as a
description of what already exists in production and as a fallback if
`deploy/`'s tooling is ever unavailable.

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
  --set-env-vars="MODE=production,FIREBASE_PROJECT_ID=PROJECT_ID,INSTANCE_CONNECTION_NAME=PROJECT_ID:REGION:SQL_INSTANCE,DB_USER=DB_APP_USER,DB_NAME=DB_NAME,FRONTEND_URL=https://PROJECT_ID.web.app,TENANT_ID=TENANT_ID" \
  --set-secrets="DB_PASSWORD=neurix-app-db-password:latest" \
  --allow-unauthenticated
```

### Required production settings (fail-fast on startup)

`MODE=production` turns on a startup check (`core/config.py`,
`validate_production_config()`, called from `main.py` before the app object
is even built) that raises and refuses to start if any of the following are
missing or empty — the error names exactly which ones, so a partially
configured deploy fails immediately and legibly instead of misbehaving at
request time:

- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `INSTANCE_CONNECTION_NAME`
- `FRONTEND_URL`
- `FIREBASE_PROJECT_ID`
- `TENANT_ID`

**`TENANT_ID`** is new as of this pass and must be set before deploying this
version of the code, or the service will fail to start under
`MODE=production`. Today it does one thing: it's prefixed onto every new
raw-storage object path (`{TENANT_ID}/raw/{uid}/...` in
`services/raw_storage.py`) so a shared bucket can't have two tenants'
objects collide, and a path alone tells you which tenant wrote it. It's also
intended as the basis for future per-tenant labeling/reporting as more
tenants are onboarded. Pick a short, stable slug (e.g. `acme`) — existing
raw-storage objects written before this was set keep resolving from their
own stored path, so changing it later doesn't break old files, it just
changes the prefix for new ones.

This check does not apply outside `MODE=production` — local dev against
`LOCAL_DATABASE_URL` needs none of the above.

**Optional tuning/feature settings** (not required, safe to leave unset):

- `DB_POOL_SIZE` (default `5`) / `DB_MAX_OVERFLOW` (default `10`) —
  SQLAlchemy connection pool sizing (`db/session.py`). The defaults match
  SQLAlchemy's own defaults. Tune down per Cloud Run service when several
  services share one Cloud SQL instance's `max_connections`, since the right
  pool size depends on how many instances a given tenant's service scales
  to.
- `ALLOWED_EMAIL_DOMAINS` — comma-separated list (e.g. `acme.com,acme.io`).
  Unset by default (no restriction — anyone with a valid Firebase ID token
  can sign in). When set, it only gates a brand-new `auth.users` row being
  created on first sign-in; it never affects a user who already exists.

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

**Newer mechanism, not used by production, for future tenants**:
`deploy/`'s Terraform defaults new tenants to `invoker_iam_disabled = true`
(`public_access_mode = "iam_check_disabled"`) instead of the `allUsers`
grant above — this is a per-service setting, not an IAM policy write, so it
sidesteps the Domain Restricted Sharing problem above entirely and needs
none of the time-boxed org-policy dance. See `deploy/README.md`'s "Known
unknowns → Public access mode" for how this was verified and the one
still-open question (a separate `constraints/run.managed.requireInvokerIam`
org policy that could still force the check on). Production's existing
`allUsers` binding predates this and is left untouched — `allow_unauthenticated`
stays supported in `deploy/`'s Terraform for exactly this kind of project.

**Cloud Build staging bucket — production gap.** `deploy/`'s Terraform
(`terraform/platform`) normally creates a `PROJECT_ID-build-staging` bucket
that a custom Cloud Build service account needs (Cloud Build rejects the
project's default `PROJECT_ID_cloudbuild` bucket when a non-default SA is
used, and requires an explicit logging destination — see `deploy/README.md`'s
"Cloud Build staging bucket (production gap)"). Since `platform/` was never
applied to `neurix-enterprise`, **this bucket does not exist in production**.
*(Needs confirmation: whether `neurix-cloudbuild-run`, the dedicated build SA
production already uses per Gotcha #1 above, still hits this bucket problem
on a fresh `gcloud run deploy`/`gcloud builds submit` today — if it does,
create the bucket by hand the same way `deploy/README.md` describes before
relying on a custom Cloud Build SA for a production rebuild.)*

## 12. Deploy frontend to Firebase Hosting

Register a Web App and get its SDK config (values here are safe to handle
in plaintext — Firebase Web API keys are meant to be public):

```bash
npx firebase-tools apps:create WEB "Neurix Enterprise Web" --project=PROJECT_ID
# -> App ID: 1:...:web:...

npx firebase-tools apps:sdkconfig WEB <APP_ID> --project=PROJECT_ID > /tmp/firebase-config.json
```

**There is no `gcloud firebase apps` command group.** Firebase Web App
management (`apps:create`, `apps:list`, `apps:sdkconfig`) only exists in
`firebase-tools` — `deploy/`'s `provision.sh` originally tried a `gcloud
firebase apps create` equivalent and had to be fixed to use `firebase-tools`
instead (`deploy/TENANT_RUNBOOK.md`'s gotcha appendix). Don't reach for a
`gcloud` equivalent here; the commands above are already the correct tool.

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

## 14. Week 2: Slack App + Gemini

`deploy/slack/slack-manifest.yml` is a reusable manifest that captures the
scopes and URLs below in one file, for **any new** Slack app going forward —
it explicitly cites this section as the source of truth for what scopes are
needed. Production's own Slack app was built by hand, one click at a time,
before that manifest existed, so nothing about the existing app needs to
change — but a new tenant's Slack app (or a from-scratch rebuild of
production's) should start from the manifest instead of repeating the
click-by-click walkthrough below; see `deploy/TENANT_RUNBOOK.md` step 4 for
that flow.

Additions to the Slack App from the Week 2 handoff (§0), which are needed by
the code as built:

- **OAuth & Permissions → Bot Token Scopes**: `commands`, `chat:write`,
  `files:read`, `im:write` (from the handoff) **plus** `im:history` (read
  thread replies in the bot DM for Edit), `users:read` and `users:read.email`
  (map a Slack user to their Neurix account by email). Reinstall the app to
  the workspace after changing scopes; the `xoxb-` token stays the same.
- **Slash Commands** `/neurix` → `https://<cloud-run-url>/slack/commands`
- **Interactivity & Shortcuts** → `https://<cloud-run-url>/slack/interactions`
- **Event Subscriptions** → on, Request URL
  `https://<cloud-run-url>/slack/events` (Slack sends a signed
  `url_verification` challenge; the backend answers it). Subscribe to bot
  events `message.im` and `file_shared`.
- **App Home** → enable the Messages tab and tick "Allow users to send
  Slash commands and messages from the messages tab", otherwise users can't
  DM files to the bot.

A Slack user must have signed in to the web app once with the same email as
their Slack profile: that is how `/neurix` finds their `user_id`.

Secrets (the handoff creates the two Slack ones; the Gemini key is new),
readable by the runtime SA:

```bash
printf '%s' "$GOOGLE_API_KEY" | gcloud secrets create gemini-api-key --data-file=- --project=PROJECT_ID
for s in slack-bot-token slack-signing-secret gemini-api-key; do
  gcloud secrets add-iam-policy-binding "$s" --project=PROJECT_ID \
    --member="serviceAccount:neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done
```

Redeploy with **CPU always allocated** — Slack work (extraction, the
post-confirm embedding/dedup/connections job, `/neurix ask`) runs as FastAPI
background tasks *after* the HTTP response, and with default CPU throttling
Cloud Run can starve them. Keep every flag from step 11 and add:

```bash
  --no-cpu-throttling \
  --update-env-vars="^;^GEMINI_MODEL=gemini-3.6-flash;GEMINI_MODEL_FALLBACKS=gemini-3.6-flash,gemini-3.5-flash" \
  --update-secrets="SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest,GOOGLE_API_KEY=gemini-api-key:latest"
```

**Gemini model** (`GEMINI_MODEL`, `GEMINI_MODEL_FALLBACKS`) is plain env
config, not a secret. The `^;^` prefix switches gcloud's list delimiter to
`;` because the fallback list itself contains commas. When a model returns
404 NotFound (Google retired it), the backend moves to the next model in
`GEMINI_MODEL_FALLBACKS` and logs `GEMINI_MODEL_FALLBACK` — search the logs
for that and update `GEMINI_MODEL` when it shows up. Only NotFound falls
back; quota/timeout errors keep langchain-google-genai's normal retry
backoff. Note that the library retries a 404 too (~62s of backoff) before
the fallback kicks in; after that the instance remembers the retired model
and skips it until the next restart/redeploy.

Verify: `gcloud run services describe neurix-backend --region=REGION
--format='value(spec.template.metadata.annotations."run.googleapis.com/cpu-throttling")'`
prints `false`.

## 15. Week 3: team, share, insights, raw files

**Migration**: run the migration runner (§5) to pick up
`0004_share_workflow_indexes.sql`. It only adds indexes (one open invite per
note+person, a note in a team space once); no column changes.

**Slack App**: no new scopes or URLs — modals and buttons use the existing
Interactivity URL, and `users.lookupByEmail` uses `users:read.email`.
`/neurix raw-access` and `/neurix file` pick the note (and person/team)
from modals, so no command arguments need escaping.

**Raw file bucket** (`view_raw` / `/neurix file`):

```bash
gcloud storage buckets create gs://PROJECT_ID-neurix-raw --project=PROJECT_ID \
  --location=REGION --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding gs://PROJECT_ID-neurix-raw \
  --member="serviceAccount:neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
# NOTE: this grants object admin on the WHOLE bucket, matching today's
# single-tenant setup (TENANT_ID only prefixes object paths within it — it
# does not isolate access). See "Preparing for a second tenant" near the
# end of this document before onboarding a second tenant onto this bucket.
# Signed URLs on Cloud Run are signed through IAM signBlob: the runtime SA
# must be allowed to create tokens for itself.
gcloud iam service-accounts add-iam-policy-binding \
  neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com --project=PROJECT_ID \
  --member="serviceAccount:neurix-backend-run@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
gcloud services enable iamcredentials.googleapis.com --project=PROJECT_ID
```

Redeploy with every earlier flag plus
`--update-env-vars=RAW_STORAGE_BUCKET=PROJECT_ID-neurix-raw` (optional:
`RAW_URL_TTL_SECONDS`, default 600). Without the bucket, ingest still
works; notes just have no original file and `/neurix file` says so. Notes
saved before this deploy have no original either.

Redeploy the frontend (§12) for the Team graph panel.

---

## 16. Web UI polish: dashboard, graph tabs + keys, Slack workspace link

**Migration**: run the migration runner (§5) to pick up
`0005_org_slack_workspace.sql`. Additive: `orgs.slack_team_id` and
`orgs.slack_workspace_url`, both nullable.

**Linking a team to its Slack workspace** (the web "Open Neurix Workspace"
button; disabled with an explanation until a team is linked):

- Automatic, no setup: the first `/neurix` command a team member runs
  links their team to that workspace (the command payload carries
  `team_id` / `team_domain`). This covers existing teams and a workspace
  installed from the Slack app dashboard.
- Optional, at install time: set Slack app → *OAuth & Permissions* →
  *Redirect URLs* to `https://BACKEND_URL/slack/oauth/callback`, and add
  env vars `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` (Secret Manager,
  from *Basic Information*). An install through "Add to Slack" / the
  shareable install URL then links the installer's team and redirects to
  `FRONTEND_URL`. If the install URL passes `redirect_uri`, also set
  `SLACK_OAUTH_REDIRECT_URI` to the same value. Unset: the callback is 404.
  The callback does not store the new bot token — the backend still uses
  the single `SLACK_BOT_TOKEN`, so this is not multi-workspace support.

No new Slack scopes. Redeploy backend and frontend (§12).

---

## 17. Preparing for a second tenant

**This section previously said none of this was implemented — that's no
longer accurate.** `deploy/`'s "Testing a tenant on the existing production
platform" mode (`deploy/README.md`) already provisions a real second tenant
against `neurix-enterprise`'s existing platform-level resources (its Cloud
SQL instance, shared bucket, Artifact Registry repo, Firebase project),
without ever running `terraform/platform` against production and without
Terraform touching, updating, or deleting anything already there. Use that
tooling — not a hand-rolled second stack — for onboarding an actual second
tenant. What it solves, and what's still open:

- **Storage bucket and IAM — solved, with one caveat.**
  `terraform/tenant/storage.tf` grants each tenant's runtime service account
  `roles/storage.objectAdmin` scoped by an IAM condition to that tenant's own
  `<tenant>/` prefix on the shared bucket (`dedicated_bucket = false`,
  default), instead of the whole-bucket grant step 15 above describes for
  today's single tenant. **Caveat, unverified**: this hasn't been confirmed
  to actually constrain *signed URLs* (`services/raw_storage.py`'s
  `signed_url()`, used by `/neurix file`) as opposed to direct API calls —
  see `deploy/README.md`'s "Known unknowns → Shared-bucket IAM condition
  (prefix isolation) — UNVERIFIED" for the concrete two-tenant test to run
  first. Until that's verified, prefer `dedicated_bucket = true` for any
  second tenant whose data actually needs to stay isolated from production's.
- **Secrets and service accounts — solved.** `deploy/`'s tenant-suffixed
  naming (`db-password-<tenant>`, `slack-bot-token-<tenant>`,
  `slack-signing-secret-<tenant>`, `gemini-api-key-<tenant>`, plus a
  per-tenant runtime SA `neurix-<tenant>-run`) is exactly this problem's
  fix — see the naming-convention note near the top of this document.
- **Frontend build-time config — solved.** `scripts/provision.sh frontend`
  (phase 5) builds and deploys each tenant's frontend separately, with its
  own `VITE_API_BASE_URL`/`VITE_FIREBASE_*` compiled in, into its own
  Firebase Hosting site — and does so without ever writing into `frontend/`,
  specifically so it can't collide with a normal production frontend build.
  See `deploy/README.md`'s "Frontend build/deploy isolation".
- **Database — solved for the "one DB per tenant" model.** Each tenant gets
  its own `neurix_<tenant>` database and `app_<tenant>` user on the shared
  Cloud SQL instance, with the grants from §6 applied per tenant via
  `grant_tenant.py`. Whether a *shared* database with row-level scoping is
  ever needed instead remains an open design question this mode doesn't
  address — it only implements one-database-per-tenant.

**This is explicitly a supervised test mode, not yet a routine "add a real
paying tenant" procedure**: `deploy/README.md`'s own pre-flight checklist
requires a fresh manual Cloud SQL backup first, an interactive
`NEURIX_ALLOW_PRODUCTION_PROJECT=yes` opt-in, typing the tenant id back at
multiple confirmation prompts, and a plan to `destroy.sh tenant` the test
tenant when done. Read `deploy/README.md`'s full "Testing a tenant on the
existing production platform" section — including its pre-flight checklist
and the read-only `gcloud`/`gcloud secrets`/`gcloud artifacts` lookups it
has you run first — before provisioning anything against production this
way.

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

**Removing a Cloud Run service (any tenant, including a test tenant from
§17).** `deletion_protection` on `google_cloud_run_v2_service` blocks direct
deletion the same way Cloud SQL's `deletion_protection`/`prevent_destroy`
does (this project defaults it to `true` for exactly this reason — see
`terraform/tenant/variables.tf`'s `cloud_run_deletion_protection`). A plain
`gcloud run services delete` on a Terraform-managed service, or a bare
`terraform destroy`, fails until that flag is flipped off first. Under
`deploy/`'s tooling this is automatic: `scripts/destroy.sh tenant` flips
`cloud_run_deletion_protection=false` in a targeted `apply` before
destroying (and skips it harmlessly with a "No Cloud Run service in state"
log line if the service was never created) — see `deploy/README.md`'s
"Destroying". For a resource not under Terraform (e.g. if production's own
Cloud Run service is ever migrated under Terraform management later), the
equivalent is removing/flipping the protection setting through whatever
mechanism manages it before the delete will succeed.
