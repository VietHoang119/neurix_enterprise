# New tenant deployment — step-by-step walkthrough

This is the practical, do-this-then-that checklist for standing up one new
tenant end to end, written from a real terminal session that provisioned
tenant `pilot` on `neurix-org` (Sep 30) — exercising all 5 phases plus every
fix that has landed in this package so far.

It assumes you've already read `README.md`'s "The 5 phases" table and
"Prerequisites" — this file doesn't re-explain *why* each phase exists or
what it's safe to re-run; it's the sequence of commands and manual clicks,
plus the real failures hit along the way and how they were resolved. For
design rationale, the full verification log, and anything marked as an open
question, see `README.md` (linked from each step below rather than repeated
here).

**Applies when:** the GCP project has already run `terraform/platform` at
least once. For a brand-new project, phase 1a (`platform`) below is part of
this same flow — see README's phase table.

---

## 0. One-time setup — not repeated per tenant

- A dedicated `gcloud` configuration for the project avoids mixing it up
  with another project (e.g. production) you may also have configured:
  ```bash
  gcloud config configurations create neurix-org
  gcloud config set project neurix-org
  gcloud config set account <your-account-email>
  ```
- The Terraform state bucket (see README's "Prerequisites" for why Terraform
  can't create this itself):
  ```bash
  gcloud storage buckets create gs://neurix-org-tfstate --project=neurix-org --location=asia-southeast1 --uniform-bucket-level-access
  gcloud storage buckets update gs://neurix-org-tfstate --versioning
  ```

## 1. Pick a `tenant_id`

Terraform's own validation requires 2-15 chars, lowercase, starting with a
letter (`scripts/lib.sh`'s `validate_tenant_id`). Two more things to know
before you pick one, both real findings:

- **A tenant id that is (or contains only) a common English word can fail
  Firebase Hosting site creation even though it's syntactically valid** —
  see README's "Firebase Hosting site ID collisions", which documents
  exactly this happening for tenant id `demo` (`Invalid name` from Google,
  which looks like a format error but isn't). Pick something
  project-specific, not a bare common word.
- **A separate, code-enforced denylist** (`neurix`, `neurix-demo-team`,
  `default`, `prod`, `production`, `main`) applies only when provisioning
  against the production project (`--project-id neurix-enterprise`) — see
  README's "Testing a tenant on the existing production platform" → step 5.
  It is unrelated to the Firebase word-collision issue above (a different
  mechanism catches it, for a different reason); don't conflate the two.

## 2. Authentication — three separate mechanisms, each expiring independently

Workspace/SSO accounts commonly time out gcloud sessions. This package
touches Google Cloud through **three separate login mechanisms**, and an
auth error only tells you a command failed, not which one to re-run:

```bash
gcloud auth login                        # gcloud commands you type directly
gcloud auth application-default login    # Terraform (via the google provider's ADC)
npx firebase-tools login --reauth        # firebase-tools calls: phase 5's apps:create/apps:sdkconfig, hosting deploy
```

If a command throws `invalid_rapt` / "Reauthentication required" / a
firebase-tools 401, identify which of the three actually issued the failing
call before re-authenticating — re-running `gcloud auth login` doesn't help
if it was `firebase-tools` (or ADC) that expired.

Also worth checking before starting a new phase, especially if you work
across more than one GCP project:
```bash
gcloud config get-value project   # must be the project you intend to touch
```

## 3. Run the 5 phases

```bash
cd deploy
cp terraform/tenants/example.tfvars terraform/tenants/<TENANT_ID>.tfvars
```

Fill in the new file (`project_id`, `tenant_id`, `region`, `state_bucket` —
see `example.tfvars` for every available field).

> **Switching this Terraform working directory to a different tenant than
> the last command you ran here?** `terraform/tenant/.terraform/` caches the
> previous run's backend config (bucket + state prefix) locally, separately
> from the tfvars file. Since `provision.sh` doesn't pass `-reconfigure`,
> re-running against a different tenant_id/project_id without clearing that
> cache fails with `Backend configuration changed`. Fix:
> ```bash
> rm -rf terraform/tenant/.terraform
> ```
> This is safe — it's a local cache of provider binaries and backend wiring,
> not the state itself (which lives in the GCS bucket).

Run each phase in order, reading every `plan` carefully — every resource
should be `+ create`, never `destroy`/`replace` (see README's "Every phase
is idempotent" note):

```bash
# Phase 1a — only if this project has never run platform/ before
scripts/provision.sh platform --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate

# Phase 1b — tenant DB/user, empty secret shells, tenant service account
scripts/provision.sh tenant-resources --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate

# Phase 2 — migrations + grants, build & push the backend image
scripts/provision.sh migrate --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate

# Phase 3 — MANUAL PAUSE: create the Slack app (Step 4 below), then paste the tokens when prompted
scripts/provision.sh slack-secrets --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate

# Phase 4 — Cloud Run + Firebase Hosting site
scripts/provision.sh app --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate
```

**If Phase 4 fails with a Hosting site `Invalid name` / "reserved by another
project" error**, this is the collision case README's "Firebase Hosting
site ID collisions" describes in full (including *why* the computed default
can still collide, and the import path if a site was already hand-created).
The short version: add the override Google itself suggests in the error
message to the tenant's tfvars and re-run this same command —
`frontend_url`/CORS pick it up automatically:
```hcl
hosting_site_id = "<name-google-suggested>"
```

```bash
# Phase 5 — build + deploy the frontend, prints the Cloud Run URL and the 3 Slack URLs
scripts/provision.sh frontend --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate
```

## 4. Create the Slack App (Phase 3's manual pause)

`slack/slack-manifest.yml`'s own header comment is the authoritative
walkthrough (`provision.sh`'s `slack-secrets` phase prints the same steps at
the right time) — summarized here for reference:

1. **api.slack.com/apps** → **Create New App** → **From an app manifest** →
   pick the tenant's workspace.
2. Paste `deploy/slack/slack-manifest.yml` as-is. Its `slash_commands.url`
   and `oauth_config.redirect_urls` are placeholders — that's fine, Slack
   doesn't verify those at creation time. **Do not** add an
   `event_subscriptions` block yet: Slack actively verifies that URL with a
   live challenge the moment you save one, and the Cloud Run service
   doesn't exist until phase 4.
3. **Create App** → **Install to Workspace**.
4. **OAuth & Permissions** → copy the **Bot User OAuth Token** (`xoxb-...`).
5. **Basic Information** → **App Credentials** → copy the **Signing
   Secret**.
6. Back in the terminal, paste both values when `slack-secrets` prompts for
   them (hidden input, stored directly in Secret Manager — see README's
   "Things Terraform must NOT do" for why this never touches `.tfvars` or
   Terraform state).
7. **App Home** → enable the **Messages Tab** and check "Allow users to
   send Slash commands and messages from the messages tab" — not settable
   from the manifest, only in the UI. Skipping this means users can't DM
   files to the bot.

## 5. After Phase 5 — finish the Slack App with the real URLs

Phase 5 prints three URLs shaped like:
```
Slash Commands      -> https://<cloud-run-url>/slack/commands
Interactivity URL   -> https://<cloud-run-url>/slack/interactions
Event Subscriptions -> https://<cloud-run-url>/slack/events
```

If you lose them, re-derive the base URL:
```bash
gcloud run services describe neurix-backend-<TENANT_ID> --region=asia-southeast1 --project=neurix-org --format="value(status.url)"
```

In the Slack App configuration:
1. **Slash Commands** → edit `/neurix` → paste the first URL → Save.
2. **Interactivity & Shortcuts** → paste the second URL → Save.
3. **Event Subscriptions** → toggle On → paste the third URL → wait for a
   green **"Verified"** (the backend answers the challenge automatically —
   see `backend/routers/slack.py`) → **Subscribe to bot events** → add
   `message.im` and `file_shared` → Save.
4. **OAuth & Permissions** → **Reinstall to Workspace** (required after any
   scope or Event Subscriptions change).

## 6. Remaining manual step (once per project, not per tenant)

Enable Google Sign-in for Firebase Auth — see README's "Known unknowns" →
"Firebase Auth Google sign-in provider" for why this has no CLI/Terraform
path. Firebase Console → project → **Authentication** → **Sign-in method**
→ **Add provider** → **Google** → **Enable** → set a support email →
**Save**.

## 7. Verify it actually works

In the tenant's Slack workspace:
```
/neurix save <some test content>
/neurix ask <a related question>
```

Open `https://<hosting-url>` (printed at the end of phase 5), sign in with
Google, and confirm the node list / graph render.

```bash
curl -i https://<cloud-run-url>/health
```
Expect **401**, not 403 — `/health` requires a valid Firebase ID token
(`backend/routers/health.py`), so a 401 confirms the service is reachable
and enforcing its own app-level auth, while a 403 would mean the Cloud Run
public-invoker setup is broken. `scripts/smoke_test.sh` (see its own header
comment) automates this exact check plus the Hosting URL and per-tenant
secret checks — worth running instead of doing this by hand:
```bash
scripts/smoke_test.sh --project-id neurix-org --tenant-id <TENANT_ID> --state-bucket neurix-org-tfstate
```

## 8. If this tenant was provisioned against production — confirm nothing else moved

Only relevant when following README's "Testing a tenant on the existing
production platform" mode. After every test round:

```bash
gcloud run revisions list --service=neurix-backend --region=asia-southeast1 --project=neurix-enterprise --limit=3
curl -s https://neurix-enterprise.web.app | grep -o 'index-[A-Za-z0-9_-]*\.js'
```
Neither should show a new revision or a changed bundle filename.

```bash
git -C <path-to-your-neurix_enterprise-checkout> status --short frontend/
```
Only `.firebase/` (a local cache) should appear — never `.firebaserc` or
`firebase.json`. This matters because "Frontend build/deploy isolation" in
README documents exactly why this phase must never write into `frontend/`;
this is the check that confirms it held in practice.

---

## Appendix — gotchas hit during live testing

For each: where it's already fixed in code, and where to read the full
explanation if you want it (to avoid saying the same thing twice).

| Symptom | Cause | Status / where it's explained |
|---|---|---|
| `Backend configuration changed` when re-running Terraform against a different tenant | Stale `terraform/tenant/.terraform/` cache still points at the previous tenant's backend config; `provision.sh` doesn't pass `-reconfigure` | **Not a bug, expected Terraform behavior.** Fix: `rm -rf terraform/tenant/.terraform` (Step 3 above — not otherwise documented in `README.md`) |
| Firebase Hosting `Invalid name` on a name that looks well-formed | A word Firebase blocks (confirmed: `demo`), or a site id already created-then-deleted before | Fixed (hashed default) + documented in full, including the recovery override → README "Firebase Hosting site ID collisions" |
| `invalid_rapt` / "Reauthentication required" | One of three independent login sessions expired | Not a bug — see Step 2 above (not otherwise documented in `README.md`) |
| Cloud Build `invalid bucket ..._cloudbuild` | A custom Cloud Build service account can't use the project's default `_cloudbuild` bucket | Fixed (dedicated `build_staging` bucket from `platform/`) → README "Things Terraform must NOT do" is silent on this; full explanation is in README "Testing a tenant on the existing production platform" → "Cloud Build staging bucket (production gap)" |
| `cannot destroy service without deletion_protection=false` (Cloud Run) | `cloud_run_deletion_protection` defaults to `true` so a stray `apply` can't drop the service | Fixed — `destroy.sh` flips it automatically before destroying (`scripts/destroy.sh`, `destroy_tenant()`); a "No Cloud Run service in state; skipping" log line is the expected, non-error message when phase 2 was never applied for that tenant — not otherwise documented in `README.md`'s "Destroying" section |
| `role ... cannot be dropped because some objects depend on it` during tenant destroy | Cloud SQL destroyed the database and the user in parallel; the role still held privileges when its drop ran | Fixed via `depends_on = [google_sql_user.tenant]` on `google_sql_database.tenant` (`terraform/tenant/main.tf`, commented in place) — not otherwise called out in `README.md` prose |
| `gcloud firebase apps create ...`: "Invalid choice" | There is no `gcloud firebase apps` command group | Fixed — uses `firebase-tools apps:list`/`apps:create`/`apps:sdkconfig` instead (rationale commented in `scripts/provision.sh`'s `phase_app`/frontend-prep code) — not otherwise mentioned in `README.md` |
| `403` when testing public Cloud Run access | Org policy `iam.allowedPolicyMemberDomains` blocks granting `roles/run.invoker` to `allUsers` | Fixed — `public_access_mode` defaults to `"iam_check_disabled"` (`invoker_iam_disabled = true`), which isn't an IAM policy write and isn't subject to that org policy → full detail and live confirmation in README "Known unknowns" → "Public access mode" |

### Still open — not yet fixed, don't treat these as resolved

- **Firebase Hosting site id collisions across tenants in the same
  project are reduced, not eliminated.** The default hash is derived only
  from `project_id`, so if a tenant name that hashes to the same suffix was
  ever created-then-deleted, a later tenant can still collide. See README
  "Firebase Hosting site ID collisions" for the full explanation and the
  `hosting_site_id` override to use if it happens again.
- **Shared-bucket IAM-condition isolation for signed URLs is unverified.**
  The prefix-scoped IAM condition on the shared raw bucket has not been
  confirmed to actually constrain signed URLs (as opposed to direct API
  calls) — see README "Known unknowns" → "Shared-bucket IAM condition
  (prefix isolation) — UNVERIFIED" for the concrete two-tenant test
  procedure to run before trusting `dedicated_bucket = false` for any
  tenant where isolation actually matters.
