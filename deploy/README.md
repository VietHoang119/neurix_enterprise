# Neurix Enterprise — tenant deployment automation

Semi-automated provisioning for one complete Neurix Enterprise tenant, built
from `docs/DEPLOYMENT_RUNBOOK.md` — the manual runbook that was verified
step by step against the real `neurix-enterprise` deployment. About 80% of
that runbook is automated here; Slack app creation and the token handoff
stay manual on purpose, because Slack requires a real admin click to
install an app into a workspace.

## Hard safety rule

**`terraform/platform` may never run against `neurix-enterprise`, under any
mode, with no override.** That project's platform-level resources (Cloud SQL
instance, Artifact Registry, shared bucket, enabled APIs, Firebase project
link) were created by hand, have no Terraform state, and the instance holds
real tenant data — pointing `platform/` at it would try to create duplicate
resources or manage things it knows nothing about.

- `terraform/platform/variables.tf`'s `project_id` variable has a
  `validation` block that refuses `project_id = "neurix-enterprise"` with a
  clear error, unconditionally — there is no variable or flag that changes
  this.
- Every script's platform-touching code path (`provision.sh platform`,
  `provision.sh all`, `destroy.sh platform`, `destroy.sh all`,
  `smoke_test.sh bucket-isolation`) calls `guard_not_production` (in
  `scripts/lib.sh`) before doing anything else, which independently and
  unconditionally refuses the same project id.
- This package is primarily meant to be tested end-to-end (including
  `platform/`) against a **brand-new, empty GCP project** you create
  yourself for that purpose.

**`terraform/tenant` has one narrow, explicitly opted-in exception**: it may
run against `neurix-enterprise` to provision (or destroy) a single test
tenant on top of the platform-level resources that already exist there by
hand — never to create or modify those platform-level resources themselves.
This is "external platform mode" — see "Testing a tenant on the existing
production platform" below for the full opt-in gate, the read-only lookups
needed, and what stays untested by this mode. The two guards above are
unchanged for `platform/`; `tenant/`'s own guards (Terraform-side:
`terraform/tenant/platform_source.tf`'s `production_project_guard`;
shell-side: `scripts/lib.sh`'s `guard_tenant_production_opt_in`) are what
grant this one exception, and only when every layer of that gate is
satisfied.

## Layout

```
deploy/
├── README.md                     # this file
├── terraform/
│   ├── versions.tf, backend.tf   # reference copies — see note below
│   ├── platform/                 # once per GCP project
│   ├── tenant/                   # once per tenant
│   │   └── platform_source.tf    # remote_state OR external platform_mode — see below
│   └── tenants/
│       ├── example.tfvars        # committed template; real *.tfvars are gitignored
│       └── test1.tfvars.example  # committed template for external/production mode
├── scripts/
│   ├── lib.sh                        # guard, logging, secret-input helpers
│   ├── provision.sh                  # the 5 phases below, each callable alone
│   ├── destroy.sh                    # tenant / platform / all teardown
│   ├── smoke_test.sh                 # post-deploy checks (+ bucket isolation test)
│   ├── grant_tenant.py               # DB grants (see "Grants" below)
│   ├── check_create_only_plan.py     # Terraform safety net (see below)
│   ├── check_tenant_scoped_state.py  # destroy.sh external-mode safety net (see below)
│   └── sql/grant_app_user.sql.tmpl
├── slack/slack-manifest.yml      # app manifest TEMPLATE, not auto-applied
└── .gitignore                    # state, real tfvars, anything secret-shaped
```

**Deviation from the originally sketched layout**, and why: `terraform/versions.tf`
and `terraform/backend.tf` are documentation-only reference copies.
`terraform/platform/` and `terraform/tenant/` are each applied as their own
Terraform root module (separate `terraform init`, separate state) — Terraform
has no mechanism to share a file between two root modules, so the real
`versions.tf`/`backend.tf` are duplicated verbatim into both
`terraform/platform/` and `terraform/tenant/`. If you change provider
versions or the backend type, change all three copies together.

## Prerequisites

- Terraform >= 1.6, a `google` CLI (`gcloud`, authenticated:
  `gcloud auth login` + `gcloud auth application-default login`), `python3`,
  `npm`, and `firebase-tools` (via `npx firebase-tools`, no global install
  needed).
- A GCS bucket to hold Terraform state (create once, by hand — Terraform
  cannot create the bucket it's about to store its own state in):
  ```bash
  gcloud storage buckets create gs://YOUR_TF_STATE_BUCKET --project=YOUR_TEST_PROJECT --location=asia-southeast1 --uniform-bucket-level-access
  ```
- A brand-new GCP project, billing enabled, **not** `neurix-enterprise`.
- Copy `terraform/tenants/example.tfvars` to `terraform/tenants/<tenant_id>.tfvars`
  and fill in `project_id`, `state_bucket`, `tenant_id` (never commit the copy
  — `.gitignore` already excludes every `terraform/tenants/*.tfvars` except
  the two committed `*.tfvars.example` files).

(To instead provision a tenant against the existing production platform, see
"Testing a tenant on the existing production platform" below — it has its
own prerequisites, state bucket, and tfvars template.)

## The 5 phases

Run each with `scripts/provision.sh <phase> --project-id ID --tenant-id TID
--state-bucket BUCKET [--region REGION] [--tfvars FILE]`, or
`scripts/provision.sh all ...` to run every phase in order (it still pauses
interactively at phase 3). Every phase is idempotent — re-running it must not
break anything (Terraform `apply` is naturally idempotent; the migration
runner and grants script are explicitly designed to be safe to re-run; the
Slack-secrets phase skips re-prompting only if you say no when asked to
overwrite).

| # | Phase (CLI name) | What | Manual? |
|---|---|---|---|
| 1a | `platform` | Terraform: APIs, Cloud SQL instance, Artifact Registry, shared bucket, Cloud Build SA, Firebase project link. Once per GCP project. | No |
| 1b | `tenant-resources` | Terraform: tenant database + user, Secret Manager shells, runtime service account, storage grant. `enable_app=false` — no Cloud Run/Hosting yet. | No |
| 2 | `migrate` | Runs `backend/scripts/run_migrations.py` against the new tenant DB as `postgres`, applies grants (see below), builds+pushes the backend image via `gcloud builds submit`. | No |
| 3 | `slack-secrets` | **Manual pause.** Prints exactly what to do in the Slack UI (create app from `slack/slack-manifest.yml`, install it, copy the bot token + signing secret), then prompts for them (hidden input) and stores them in Secret Manager. | **Yes** |
| 4 | `app` | Terraform: Cloud Run service (image from phase 2, secrets from phase 3) + Firebase Hosting site. | No |
| 5 | `frontend` | Builds the frontend with per-tenant `VITE_API_BASE_URL`/`VITE_FIREBASE_*` (compiled in at build time), deploys to the tenant's Hosting site, prints the Cloud Run URL and the three Slack URLs to paste back into the Slack app. | No (prints a manual follow-up) |

Two things stay manual no matter what (see "Known unknowns" below): Slack
app creation/install (phase 3, by design), and enabling the Google sign-in
provider in Firebase Auth (no reliable CLI/Terraform path — both `app` and
`frontend` phases print a reminder).

## Things Terraform must NOT do (and doesn't)

- **No SQL migrations or grants.** That's `scripts/provision.sh migrate`'s
  job (`backend/scripts/run_migrations.py` + `grant_tenant.py`), not
  Terraform's. Terraform only creates the empty database and the DB user/
  role via the Cloud SQL Admin API.
- **No Slack/Gemini secret values in `.tfvars` or Terraform state.**
  `terraform/tenant/main.tf` creates `slack-bot-token-<tenant>` and
  `slack-signing-secret-<tenant>` as empty Secret Manager shells (no
  `google_secret_manager_secret_version` at all). Values are added later by
  `scripts/provision.sh`'s `slack-secrets` phase directly via `gcloud
  secrets versions add`, using `printf '%s'` piped to `--data-file=-` —
  **never** `echo` or a here-string, because either one appends a trailing
  newline that silently breaks the Slack signing-secret HMAC check (this
  happened before in the manual deployment).
  `gemini-api-key-<tenant>` follows the same empty-shell pattern **only in
  the normal (`platform_mode = "remote_state"`) flow**; in external platform
  mode no Gemini secret is created at all — this tenant's runtime service
  account is instead granted read access to an **existing** secret named by
  `external_gemini_api_key_secret_id` (see "Testing a tenant on the existing
  production platform").
  The one exception is `db-password-<tenant>`: its value is a Terraform
  `random_password`, so it necessarily exists in Terraform state — there's
  no way to generate it outside Terraform and still have Terraform own the
  matching `google_sql_user`.

## Grants: ending the "forgot GRANT after migration" failure class

Every past permissions incident in this project's history was the same
thing: a migration landed, the runbook's manual `GRANT` step (§6) didn't get
re-run, and the app got permission-denied errors at request time instead of
at deploy time.

The fix implemented here (`scripts/sql/grant_app_user.sql.tmpl`, applied by
`scripts/grant_tenant.py` right after the migration runner):

```sql
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_<tenant>;
-- (+ auth schema, + sequences, + schema USAGE)
```

`ALTER DEFAULT PRIVILEGES FOR ROLE postgres` means every table/sequence a
**future** migration creates, as `postgres` (the same role the runner always
connects as), is automatically granted to the app user — no explicit grant
step needed ever again, for 0006 and beyond.

**This was verified, not assumed.** A local Postgres 16 + pgvector instance
was stood up in this sandbox (`LOCAL_DATABASE_URL`, no Cloud SQL available
here) and:

1. `backend/scripts/run_migrations.py` was run for real — all 5 real
   migrations (`0001`-`0005`) applied cleanly.
2. `grant_tenant.py` was run for a test app user; confirmed permission
   **denied** before, **granted** after, for an existing table (`node_pool`)
   and the `auth` schema.
3. A throwaway migration (`0006_throwaway_...sql`, deleted immediately
   after, never committed) created a brand-new table with a `serial` PK.
   `run_migrations.py` was re-run to apply it — **without re-running any
   grant step** — and the test app user could immediately `INSERT`/`SELECT`
   on that brand-new table and its sequence.

No case was found where this doesn't work cleanly, so there is no fallback
explicit-grant-after-every-migration step — the ask was "if you find a
reason this can't work cleanly, say so and fall back"; no such reason was
found in testing.

## Known unknowns — verify, don't assume

### Firebase Auth Google sign-in provider

**Confirmed not cleanly automatable.** Enabling the Google sign-in provider
in Firebase Auth needs a click in the Firebase Console (Authentication ->
Sign-in method -> Add provider -> Google -> Enable -> set a support email ->
Save) — there is no Terraform resource or reliable `gcloud`/`firebase` CLI
command for this specific action as of this writing (it typically needs an
OAuth client created in the console). This is intentionally left as an
explicit manual step: both `provision.sh app` and `provision.sh frontend`
print a reminder for it. Do this once per GCP project (or once per tenant,
if a tenant needs its own restriction), before end-to-end sign-in testing.

### Shared-bucket IAM condition (prefix isolation) — UNVERIFIED

`terraform/tenant/storage.tf` grants a tenant's runtime service account
`roles/storage.objectAdmin` on the **shared** raw bucket, scoped by an IAM
condition to objects under that tenant's own `<tenant>/` prefix (used when
`dedicated_bucket = false`, the default). This has **not** been confirmed to
actually isolate tenants for the specific case of **signed URLs**
(`services/raw_storage.py`'s `signed_url()`, which goes through IAM
`signBlob`) — a condition that correctly blocks a direct API call may not
equally constrain what can be *signed*, since signing and using a signed URL
are different code paths in GCS.

**Concrete test procedure** (`scripts/smoke_test.sh bucket-isolation`
attempts an automated version of this, but flags it as best-effort — a real
verification needs actual key material and should be run by hand too):

1. Provision two tenants, `a` and `b`, both with `dedicated_bucket = false`
   (sharing the same platform bucket).
2. As tenant `a`'s runtime service account
   (`neurix-a-run@PROJECT.iam.gserviceaccount.com`), attempt to sign a GET
   URL for an object under `b/raw/...` in the shared bucket — e.g. via
   `gcloud storage sign-url` with `--impersonate-service-account`, or by
   temporarily deploying tenant `a`'s Cloud Run revision and calling its
   `view_raw`-equivalent code path with a path under `b/`'s prefix.
3. **Expected**: denied. If it succeeds, the IAM condition is not actually
   isolating tenants for signed URLs, and any tenant that needs real
   isolation should be given `dedicated_bucket = true` instead until this is
   fixed.
4. Repeat for an object genuinely under `a`'s own prefix — **expected**:
   succeeds — to confirm the condition isn't simply blocking everything.

Until this is verified, treat `dedicated_bucket = false` as convenient for
testing/cost, not as a proven tenant-isolation boundary.

### Cost

The `platform` phase creates a Cloud SQL instance (`db-g1-small`,
`asia-southeast1`), which runs 24/7 and bills whether used or not (order of
$36/month at current list pricing — verify against current GCP pricing
before relying on that number). **Run `scripts/destroy.sh` after testing.**
See the "Post-deploy cleanup checklist" in `docs/DEPLOYMENT_RUNBOOK.md` for
the same reminder in the original manual flow (stopping the instance between
sessions instead of destroying it is also an option if you're going to test
again soon: `gcloud sql instances patch --activation-policy=NEVER`).

### Firebase Hosting multi-site deploy mechanics

`scripts/provision.sh`'s `frontend` phase uses `firebase target:apply
hosting <tenant> <site-id>` (writes to `frontend/.firebaserc`'s `targets`
map — the same file the original runbook already edits by hand for
`projects.default`, so this isn't new territory for that file) followed by
`firebase deploy --only hosting:<tenant>`, relying on Firebase's documented
hosting-targets model where a single untargeted `hosting` block in
`frontend/firebase.json` applies to whichever site the `--only` flag
resolves via `.firebaserc`. **This could not be verified against a live
Firebase project in this environment** (network access to
`firebase.google.com` docs and no real Firebase project were both
unavailable here). If it errors on your real run, the documented fallback is
to generate a temporary copy of `firebase.json` with an explicit
`"site": "<site-id>"` key added to its `hosting` block and deploy with
`firebase deploy --only hosting -c <that copy>` from within `frontend/`.

## Destroying

```bash
scripts/destroy.sh tenant   --project-id ID --tenant-id TID --state-bucket BUCKET
scripts/destroy.sh platform --project-id ID --state-bucket BUCKET   # after every tenant is gone
scripts/destroy.sh all      --project-id ID --tenant-id TID --state-bucket BUCKET
```

`destroy.sh platform` first flips `sql_deletion_protection` to `false` in
its own `apply` (Terraform refuses to destroy a `google_sql_database_instance`
while that's `true`, which is the default — on purpose, so a stray `apply`
can never silently drop the database), then destroys.

## Testing a tenant on the existing production platform

This mode provisions (or destroys) **one test tenant** on top of the
platform-level resources (`neurix-enterprise-db`, the shared bucket, the
Artifact Registry repo, the Firebase project) that already exist by hand in
`neurix-enterprise` — without ever running `terraform/platform` against that
project, and without letting Terraform touch, update, or delete anything
that's already there. It exists to test this package's `tenant/` flow for
real, on the actual Standard-tier design (one shared Cloud SQL instance, one
database per tenant), without paying for a second instance.

**Production holds real data. Read this whole section, and do the pre-flight
checklist, before running anything.**

### 1. External platform mode — the design

`terraform/tenant` supports two ways of learning its shared platform stack's
identifiers, chosen by the `platform_mode` variable (default
`"remote_state"`):

- **`"remote_state"`** (the normal flow, unchanged): reads every platform
  output via `terraform_remote_state`. Requires `platform/` to have already
  been applied to the same project.
- **`"external"`**: accepts the same facts as plain `external_*` variables
  instead, and never reads any platform Terraform state. This is what makes
  running `tenant/` against `neurix-enterprise` possible at all — production
  has no Terraform state for its platform-level resources to read.

This was the cleanest option available (versus, say, a second `tenant/`
module or hand-editing `terraform_remote_state`'s backend config per run):
one variable toggles a `count`-gated data source and a couple of `locals`
blocks (`terraform/tenant/platform_source.tf`) — every other resource in the
module is unaware of which mode is active, since it only ever reads
`local.platform.*`.

In external mode, `platform_source.tf`'s `terraform_data.external_mode_guard`
fails `plan`/`apply` up front, by name, if a required `external_*` input was
left unset. The Gemini API key is handled differently in this mode: instead
of creating a new `gemini-api-key-<tenant>` secret (as `remote_state` mode
does), the tenant's runtime service account is only granted
`roles/secretmanager.secretAccessor` on the **existing** secret named by
`external_gemini_api_key_secret_id` — no new secret, no new version, ever
written by this module in external mode.

### 2. Read-only lookups — collect every input value first

None of these values are guessed or assumed anywhere in this package. Run
these against `neurix-enterprise` (read-only; none of them change anything)
and copy the results into your tenant's `.tfvars`:

```bash
# Cloud SQL instance name + connection name
gcloud sql instances describe neurix-enterprise-db --project=neurix-enterprise \
  --format='value(name,connectionName)'

# Artifact Registry repository (region-docker.pkg.dev/PROJECT/REPO)
gcloud artifacts repositories list --project=neurix-enterprise --location=asia-southeast1

# Shared raw-files bucket
gcloud storage buckets list --project=neurix-enterprise --format='value(name)'

# Existing Gemini API key secret name
gcloud secrets list --project=neurix-enterprise --format='value(name)' | grep -i gemini

# Firebase project id (normally == the GCP project id)
gcloud firebase projects list --format='value(projectId)' | grep neurix-enterprise

# Optional: an existing Cloud Build service account to reuse
gcloud iam service-accounts list --project=neurix-enterprise --format='value(email)' | grep -i cloudbuild
```

### 3. Set up the test tenant's tfvars

```bash
cp terraform/tenants/test1.tfvars.example terraform/tenants/test1.tfvars
# fill in state_bucket and every external_* value from step 2 above —
# never commit this file (deploy/.gitignore already excludes it)
```

`terraform/tenants/test1.tfvars.example` is committed with conservative
resource limits for a test tenant sharing production's `db-g1-small`
instance: `cloud_run_max_instances = 1`, `cloud_run_min_instances = 0`,
`db_pool_size = 2`, `db_max_overflow = 3` (vs. the normal-flow defaults of
3/0/5/10) — this is a test, not a tenant that needs to scale.

### 4. State bucket bootstrap

A **separate** state bucket from any normal-flow test project's bucket is
recommended, so this mode's state can never be confused with another
project's. Terraform cannot create the bucket it's about to store its own
state in, so create it by hand once, with versioning on (so a bad state
write is recoverable) and uniform bucket-level access:

```bash
gcloud storage buckets create gs://YOUR_PROD_TEST_TF_STATE_BUCKET \
  --project=YOUR_OWN_PROJECT_FOR_THIS_BUCKET \
  --location=asia-southeast1 \
  --uniform-bucket-level-access
gcloud storage buckets update gs://YOUR_PROD_TEST_TF_STATE_BUCKET --versioning
```

This bucket does not itself need to live in `neurix-enterprise` — any
project you control that Terraform can write to works, since the bucket
only holds this tenant's own `tenants/neurix-enterprise/test1/...` state
prefix, never platform-level state (there is none to hold).

### 5. Opt-in steps

Every tenant-only script step (`provision.sh` phases other than `platform`/
`all`; `destroy.sh tenant`; `smoke_test.sh`'s default mode) requires **all**
of the following when `--project-id neurix-enterprise`:

1. `platform_mode = "external"` in the tenant's `.tfvars` (step 3 above).
2. `NEURIX_ALLOW_PRODUCTION_PROJECT=yes` set in your shell:
   ```bash
   export NEURIX_ALLOW_PRODUCTION_PROJECT=yes
   ```
3. `tenant_id` matching `^[a-z0-9-]{3,20}$` and not on the denylist
   (`neurix`, `neurix-demo-team`, `default`, `prod`, `production`, `main`) —
   `validate_tenant_id_strict` in `scripts/lib.sh`.
4. Typing the tenant id back at an interactive prompt, after a loud banner
   naming the project, the tenant, and that this is production.

`platform` and `all` remain unconditionally refused for
`neurix-enterprise` — always run the individual tenant-only phases:

```bash
scripts/provision.sh tenant-resources --project-id neurix-enterprise --tenant-id test1 --state-bucket YOUR_PROD_TEST_TF_STATE_BUCKET
scripts/provision.sh migrate           --project-id neurix-enterprise --tenant-id test1 --state-bucket YOUR_PROD_TEST_TF_STATE_BUCKET
scripts/provision.sh slack-secrets     --project-id neurix-enterprise --tenant-id test1 --state-bucket YOUR_PROD_TEST_TF_STATE_BUCKET
scripts/provision.sh app               --project-id neurix-enterprise --tenant-id test1 --state-bucket YOUR_PROD_TEST_TF_STATE_BUCKET
scripts/provision.sh frontend          --project-id neurix-enterprise --tenant-id test1 --state-bucket YOUR_PROD_TEST_TF_STATE_BUCKET
```

The `migrate` phase prompts interactively for the Cloud SQL `postgres` user's
password — there is no known Secret Manager secret holding production's
admin password for this package to read, and guessing a name for a
production credential is exactly the kind of mistake this mode exists to
avoid.

### 6. Pre-flight checklist

- [ ] Read this entire section.
- [ ] **Take a fresh manual Cloud SQL backup of `neurix-enterprise-db`
      first**: `gcloud sql backups create --instance=neurix-enterprise-db --project=neurix-enterprise`.
      This is the actual safety net if anything goes wrong — everything
      else in this package is defense-in-depth on top of it.
- [ ] Ran every read-only lookup in step 2 and filled in
      `terraform/tenants/test1.tfvars` (never commit it).
- [ ] Picked a `tenant_id` that passes the strict pattern and isn't on the
      denylist, and isn't a real tenant you'd ever want to keep.
- [ ] `NEURIX_ALLOW_PRODUCTION_PROJECT=yes` is set in the shell you'll run
      these commands from.
- [ ] Comfortable typing the tenant id back at each production confirmation
      prompt — there are several (the top-level opt-in gate, each SQL step
      in `migrate`, and `destroy.sh tenant`).
- [ ] Ready to run `scripts/destroy.sh tenant --project-id neurix-enterprise
      --tenant-id test1 ...` when done testing (see step 7 below) — this
      test tenant should not be left running against production
      indefinitely.

### 7. Terraform safety net: create-only

Because `platform/` was never applied for this project, the very first
`terraform apply` in this mode runs against a state that already has
*something* in it (production's hand-created resources aren't in Terraform
state, but the tenant module still needs its first apply to be provably
safe). `scripts/provision.sh`'s `tf_tenant_apply` helper enforces this: it
runs `terraform plan -out=tfplan`, then `terraform show -json tfplan |
check_create_only_plan.py`, which **fails the run** if any
`resource_change` has actions other than `create` (or `no-op`/`read`) — only
then does it `terraform apply` that exact saved plan. An update, replace, or
delete of anything already there stops the run before `apply` ever runs.
This only activates when `platform_mode = "external"`; the normal flow's
`terraform apply` is unchanged (a redeploy legitimately needs `update`
actions, e.g. a new `backend_image`).

Tested with hand-made plan JSON (no cloud credentials needed):
`check_create_only_plan.py` accepts a plan containing only
`create`/`no-op`/`read` actions, and refuses one containing an `update`
action or a `replace` (`["delete", "create"]`) — see "Verification" below.

**IAM in `tenant/` — audited, none authoritative.** Every IAM resource this
module creates is listed here, and every one is a non-authoritative
`*_iam_member` resource (adds one binding without touching any other
existing binding on the same resource) — never `*_iam_binding` or
`*_iam_policy` (both of those are authoritative: they replace the *entire*
set of bindings for a role or the *entire* policy, which on a shared
resource like the production bucket, an existing secret, or the project
itself could silently drop bindings this package knows nothing about,
including ones that keep production itself working):

| Resource | Type | Scope |
|---|---|---|
| `db_password_accessor` | `google_secret_manager_secret_iam_member` | this tenant's own secret |
| `slack_bot_token_accessor` | `google_secret_manager_secret_iam_member` | this tenant's own secret |
| `slack_signing_secret_accessor` | `google_secret_manager_secret_iam_member` | this tenant's own secret |
| `gemini_api_key_accessor` | `google_secret_manager_secret_iam_member` | **existing, shared** secret (external mode) or this tenant's own (remote_state mode) |
| `run_sql_client` | `google_project_iam_member` | the project (grants `roles/cloudsql.client` to this tenant's own SA only) |
| `run_token_creator_self` | `google_service_account_iam_member` | this tenant's own service account |
| `dedicated_object_admin` | `google_storage_bucket_iam_member` | this tenant's own dedicated bucket (`dedicated_bucket = true` only) |
| `shared_object_admin_scoped_to_prefix` | `google_storage_bucket_iam_member` | **existing, shared** bucket, condition-scoped to this tenant's own `<tenant>/` prefix |
| `public_invoker` | `google_cloud_run_v2_service_iam_member` | this tenant's own Cloud Run service |

The two resources marked "existing, shared" (`gemini_api_key_accessor` in
external mode, and `shared_object_admin_scoped_to_prefix` whenever
`dedicated_bucket = false`) are the only ones that touch a resource this
package doesn't itself create — both are still `*_iam_member`, so they only
ever *add* this tenant's own binding, never remove or replace any other
tenant's or production's own bindings on that same secret/bucket.

### 8. Destroying the test tenant

```bash
export NEURIX_ALLOW_PRODUCTION_PROJECT=yes
scripts/destroy.sh tenant --project-id neurix-enterprise --tenant-id test1 --state-bucket YOUR_PROD_TEST_TF_STATE_BUCKET
```

In external mode this is scoped to **only** this tenant's own Terraform
state (`tenants/neurix-enterprise/test1` in the state bucket) — the shared
production platform (Cloud SQL instance, Artifact Registry, shared bucket)
is never touched, because it was never in that state to begin with.
`destroy.sh` prints every resource address in the tenant's state, then
refuses (via `check_tenant_scoped_state.py`) unless every resource address
is one `terraform/tenant` can actually produce **and** the state's own
`tenant_id` output matches `--tenant-id` — catching a
`--tenant-id`/`--tfvars`/`--state-bucket` mismatch before `terraform
destroy` ever runs. It then requires typing the tenant id back once more.
**This destroys the tenant's database (`neurix_test1`) and all data in
it — permanently, with no undo** — `destroy.sh` says so explicitly before
running.

### What this mode does NOT test

- **`terraform/platform` itself** — instance creation, API enablement,
  Artifact Registry repository creation, the shared bucket, the Cloud Build
  service account, the Firebase project link. None of that runs in this
  mode; it stays genuinely untested by this package until `platform/` is
  applied for real on a throwaway project (the normal flow).
- **Firebase Auth authorized domains** for a brand-new Hosting site
  (`test1-neurix.web.app`) created under the same, already-in-use Firebase
  project. Firebase Auth restricts sign-in by authorized domain; whether a
  new Hosting site's domain needs to be added by hand to the existing
  project's authorized-domains list (Firebase Console → Authentication →
  Settings → Authorized domains) before sign-in works for this test tenant
  is unverified here — check that console page if sign-in fails after the
  `frontend` phase.
- **The shared-bucket IAM-condition signed-URL isolation** — already listed
  under "Known unknowns" below as unverified in general; it applies
  identically here (`dedicated_bucket = false`, the default, uses the
  **existing production bucket** via `external_shared_raw_bucket_name`).
  Prefer `dedicated_bucket = true` for this test tenant if isolation from
  production's own raw files actually matters for what's being tested — it
  creates a small dedicated `neurix-raw-test1` bucket instead of scoping
  into the shared one.

## Verification: what was actually run vs. only written

Real GCP credentials were not available in the environment this package was
built in, so no `terraform apply`/`gcloud` calls against real infrastructure
were possible. What *was* run for real:

- `terraform fmt -recursive` on the whole `terraform/` tree (clean).
- `terraform init` + `terraform validate` for **both** `platform/` and
  `tenant/`, against the **real** `hashicorp/google`/`hashicorp/google-beta`
  8.4.0 and `hashicorp/random` 3.9.1 provider schemas (downloaded directly
  from `releases.hashicorp.com` into a local filesystem mirror, since
  `registry.terraform.io` itself was blocked by this environment's egress
  policy — provider *binaries* are a different host and were reachable).
  Both modules validated clean against the real provider schema, which is
  what confirms attribute names actually used here — e.g.
  `google_cloud_run_v2_service`'s `resources.cpu_idle`/`startup_cpu_boost`,
  `value_source.secret_key_ref`, and `google_firebase_hosting_site`'s
  `default_url` — actually exist on this provider version.
- `terraform plan` with dummy values for both modules, using a temporary
  local-backend override (removed afterwards, nothing committed). Both
  plans got as far as fully resolving the resource graph — including
  `random_password` and the `terraform_remote_state` data source for
  `tenant/` — and then failed cleanly at Google Cloud provider
  authentication ("could not find default credentials"), which is the
  expected/correct failure with no ADC configured, not a config error.
- `shellcheck` on `lib.sh`, `provision.sh`, `destroy.sh`, `smoke_test.sh` —
  clean.
- The migration + grants flow (see "Grants" above) — run for real against a
  locally installed Postgres 16 + pgvector, including the throwaway-migration
  verification.
- `python3 -m py_compile` on `grant_tenant.py`.

**External platform mode, added in this pass — additionally verified for
real** (still without real GCP credentials):

- `terraform plan` for `tenant/` with `platform_mode = "external"` and
  `project_id = "neurix-enterprise"`, dummy `external_*` values, same local-
  backend approach as above: the plan fully resolved (`random_password.
  db_user`, `terraform_data.external_mode_guard`,
  `terraform_data.production_project_guard`, every output including the
  Gemini secret passthrough) and failed cleanly at provider authentication —
  confirming the whole conditional graph (both guard resources' `precondition`
  blocks passing, `local.platform` resolving from variables instead of
  `terraform_remote_state`) actually works, not just parses.
- The same plan **negative-tested**: `project_id = "neurix-enterprise"` with
  `platform_mode` left at its default (`"remote_state"`) correctly fails
  `terraform_data.production_project_guard`'s precondition with the intended
  error message, before ever reaching provider auth. `platform_mode =
  "external"` with every `external_*` value left unset correctly fails
  `terraform_data.external_mode_guard`'s precondition, naming every missing
  variable. A normal-flow plan (`remote_state` mode, non-production
  `project_id`) was re-run after these changes and still fails at exactly
  the same point as before (no default credentials) — confirming the new
  guards don't affect the existing flow.
- `check_create_only_plan.py` run against three hand-made plan JSON files
  (no cloud credentials needed): a plan with only `create`/`no-op`/`read`
  actions passes; a plan containing an `update` action fails, naming the
  resource; a plan containing a `replace` (`["delete", "create"]`) and a
  bare `delete` fails, naming both resources. Both the file-argument and
  stdin invocation forms were tested.
- `check_tenant_scoped_state.py` run against three hand-made state JSON
  files: a state whose resources are all known tenant-module types and
  whose `tenant_id` output matches passes; a state containing an
  unrecognized resource type fails, naming it; a state whose `tenant_id`
  output doesn't match the given tenant id fails, naming both values.
- `scripts/provision.sh` and `scripts/destroy.sh` run for real (a real
  Terraform binary and a stub `gcloud` satisfying `require_cmd`, no real GCP
  calls reached) against every guard branch: `platform`/`all` against
  `neurix-enterprise` refuse immediately with no prompt; a tenant-only phase
  against `neurix-enterprise` with `platform_mode` still `"remote_state"`
  refuses naming exactly that; the same with external mode set but
  `NEURIX_ALLOW_PRODUCTION_PROJECT` unset refuses naming exactly that; the
  same with a denylisted `tenant_id` (`prod`) refuses naming the denylist;
  the same with everything set and the tenant id typed correctly at the
  confirmation prompt passes through to a real `terraform init` (which then
  fails at "no default credentials", the expected next failure); the same
  with the tenant id mistyped at the prompt aborts before any `terraform`
  call. The existing normal flow (non-production `project_id`) was
  re-run after these changes and reaches `terraform init` with no guard
  prompts at all, exactly as before.
- `grant_tenant.py`'s new `guard_sql_target` function, unit-tested in
  isolation (imported directly, `input()` monkeypatched, no real DB
  connection): a correct tenant database name on a non-production project
  passes silently; `db_name = "neurix"` and `db_name = "postgres"` both
  refuse, naming the mismatch; on the production project, a correct database
  name with `"yes"` typed at the confirmation passes, and with anything else
  typed refuses.
- `python3 -m py_compile` on `check_create_only_plan.py` and
  `check_tenant_scoped_state.py`, alongside `grant_tenant.py`.

What was **written but not run against real infrastructure**: every
`terraform apply`/`destroy` against actual GCP resources (both the normal
flow and external platform mode), `gcloud builds submit`, the Slack app
creation/install flow, the frontend build+Hosting deploy, and
`smoke_test.sh`'s HTTP checks. This also means the *substance* of the
production-testing pre-flight checklist (a real Cloud SQL backup, the real
read-only lookups against `neurix-enterprise`) was written and reasoned
through but not physically executed here — Hoàng will do the real
end-to-end test, on a fresh GCP project first and then, following this
README's "Testing a tenant on the existing production platform" section,
against `neurix-enterprise` itself.

## Judgment calls made

- **Provider version**: pinned to `~> 8.4` for `google`/`google-beta` (the
  latest available at build time — the handoff didn't specify a version, and
  the schema was verified against this exact version, see above) and `~> 3.9`
  for `random`, rather than an older version guessed from memory.
- **Secret naming for Gemini**: the handoff didn't name this secret
  explicitly ("A Gemini key secret, also value-less in Terraform"); named it
  `gemini-api-key-<tenant>` for consistency with the other two
  Slack-secret names, which *are* specified.
- **Cloud Build service account**: created once at the **platform** level
  (`neurix-cloudbuild`), shared across tenants, rather than per-tenant —
  it only builds images, doesn't touch tenant data, and matches how
  Artifact Registry itself is shared per the handoff.
- **`FRONTEND_URL` / Hosting-Cloud-Run ordering**: Firebase Hosting site
  URLs are deterministic (`https://<site-id>.web.app`), so
  `scripts/provision.sh app` computes `frontend_url` up front from
  `<tenant>-neurix.web.app` rather than needing a Hosting site to already
  exist — this avoids a circular dependency between Cloud Run (needs
  `FRONTEND_URL` for CORS) and Hosting (created in the same Terraform phase).
- **Cloud Run ingress**: left at `INGRESS_TRAFFIC_ALL` with a public
  `allUsers` invoker binding (per the handoff — Slack and browsers call it
  directly, auth is enforced inside the app). `allow_unauthenticated` is a
  variable so this can be turned off if a project's org policy blocks public
  `allUsers` bindings (Domain Restricted Sharing — see
  `docs/DEPLOYMENT_RUNBOOK.md` step 11 gotcha #2 for the manual, time-boxed
  override procedure; Terraform can't do that override safely, it's
  deliberately a human-in-the-loop action).
- **`tenant_id` length cap (15 chars)**: not stated in the handoff; derived
  from the 30-character GCP service-account `account_id` limit combined with
  the `neurix-<tenant>-run` naming convention.
- **`platform_mode` design**: a two-value variable (`"remote_state"` /
  `"external"`) gating a `count`-conditional `terraform_remote_state` data
  source plus one `locals` block, over alternatives like a second copy of
  `tenant/` or a wrapper module — chosen because every other resource in
  the module stays unaware of which mode is active (it only ever reads
  `local.platform.*`), so the diff against the normal flow is small and
  auditable, and nothing about the normal flow's behavior changes.
- **Production `project_id` validation moved out of the Terraform variable
  block**: `tenant/variables.tf`'s `project_id` variable used to hard-refuse
  `"neurix-enterprise"` in its own `validation` block (unconditionally, no
  way to allow it). That block can't reference `var.platform_mode` safely
  across Terraform versions (cross-variable validation needs >= 1.9, and
  this package targets `>= 1.6.0`), so the conditional guard was moved into
  a resource `precondition`
  (`terraform_data.production_project_guard`), which has always supported
  referencing other variables. `platform/variables.tf`'s equivalent
  validation was deliberately left untouched — it has no carve-out to make.
- **No Terraform variable for the Cloud SQL root/admin password in external
  mode**: the handoff's external-mode input list doesn't include one, and no
  secret is known to hold production's admin password under a predictable
  name. Rather than guess a secret name for a production credential,
  `scripts/provision.sh`'s `migrate` phase prompts for it interactively
  (hidden input) in external mode instead of reading Secret Manager.
- **Cloud Build service account is optional in external mode**
  (`external_cloudbuild_service_account_email`, default `""`): production
  already has a working build/deploy flow for `neurix-backend` under some
  service account, but the handoff didn't name it and guessing would risk
  granting/assuming permissions incorrectly. Left empty, `gcloud builds
  submit` in `provision.sh`'s `migrate` phase omits `--service-account` and
  falls back to the project's default Cloud Build service account.
- **Gemini secret: grant access to an existing secret, never create one, in
  external mode** — the handoff's phrasing ("name of the existing Gemini API
  key secret to be granted to the new service account") reads as reusing a
  secret that already holds a real key, not creating an empty tenant-specific
  shell the way the normal flow does. `terraform/tenant/main.tf` only
  creates `google_secret_manager_secret.gemini_api_key` when `platform_mode
  = "remote_state"`; in external mode it grants
  `roles/secretmanager.secretAccessor` on `external_gemini_api_key_secret_id`
  instead, and `scripts/provision.sh`'s `slack-secrets` phase skips the
  Gemini prompt entirely in that mode (there is no per-tenant secret to
  write a value into).
- **Production `tenant_id` strict pattern (3-20 chars, `[a-z0-9-]`) is layered
  on top of, not instead of, Terraform's own `tenant_id` validation (2-15
  chars, must start with a letter)** — the handoff specified the former
  exactly for the shell-level opt-in gate; the latter still applies at
  `terraform plan`/`apply` time regardless (it's driven by the 30-character
  GCP service-account limit, unrelated to production-specific risk), so the
  two together are effectively the intersection: 3-15 chars, start with a
  letter, not on the denylist.
- **`smoke_test.sh`'s default mode was extended to allow the same
  production opt-in gate** (not explicitly requested in the handoff, which
  only names `provision.sh`/`destroy.sh`): it's read-only and purely
  tenant-scoped (its own state, its own Cloud Run/Hosting URLs), so refusing
  it for production seemed like an unnecessary gap given the whole point of
  this mode is testing a real tenant. `bucket-isolation` mode was
  deliberately left hard-refused — it spans two tenants and reaches into
  the platform's *shared* bucket directly via service-account impersonation,
  which is a materially different risk profile.

## Open questions for Hoàng

1. Firebase Hosting multi-site deploy mechanics (targets vs. a `site` key in
   `firebase.json`) — see "Known unknowns" above; verify on the first real
   `frontend` phase run and report back if the fallback is needed.
2. Shared-bucket IAM condition signed-URL isolation — run the two-tenant
   test above before treating `dedicated_bucket = false` as a real isolation
   boundary for any tenant that matters.
3. Whether the Cloud Build service account should also get
   `roles/artifactregistry.reader` explicitly, or whether `writer` (which
   includes read) is sufficient — used `writer` alone since it's a superset,
   but worth a second look if builds ever need to *pull* a base image from
   the same private repo (they don't today — the Dockerfile's base image is
   public `python:3.12-slim`).
4. Whether production already has a Secret Manager secret holding the Cloud
   SQL admin/root password under some name — if so, `scripts/provision.sh`'s
   `migrate` phase could read it via `gcloud secrets versions access`
   instead of prompting interactively every time. Left as an interactive
   prompt for now (see "Judgment calls made") since no such secret name was
   given and guessing one for a production credential seemed like the wrong
   default.
5. Whether there's an existing Cloud Build service account already used to
   build/deploy `neurix-backend` that should be passed as
   `external_cloudbuild_service_account_email`, rather than falling back to
   the project's default Cloud Build service account.
6. Firebase Auth authorized domains for a new Hosting site under the
   existing, already-in-use Firebase project — see "Testing a tenant on the
   existing production platform" → "What this mode does NOT test".
