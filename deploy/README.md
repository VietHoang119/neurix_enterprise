# Neurix Enterprise — tenant deployment automation

Semi-automated provisioning for one complete Neurix Enterprise tenant, built
from `docs/DEPLOYMENT_RUNBOOK.md` — the manual runbook that was verified
step by step against the real `neurix-enterprise` deployment. About 80% of
that runbook is automated here; Slack app creation and the token handoff
stay manual on purpose, because Slack requires a real admin click to
install an app into a workspace.

## Hard safety rule

**Nothing in this package may ever run against `neurix-enterprise`.** That
project was created by hand, has no Terraform state, and holds real tenant
data — pointing Terraform at it would try to create duplicate resources or
manage things it knows nothing about.

- `project_id` is a required Terraform variable with no default, and every
  module (`terraform/platform`, `terraform/tenant`) has a `validation` block
  that refuses `project_id = "neurix-enterprise"` with a clear error.
- Every script (`provision.sh`, `destroy.sh`, `smoke_test.sh`) calls
  `guard_not_production` (in `scripts/lib.sh`) before doing anything else,
  which independently refuses the same project id.
- This package is meant to be tested against a **brand-new, empty GCP
  project** you create yourself for that purpose.

## Layout

```
deploy/
├── README.md                     # this file
├── terraform/
│   ├── versions.tf, backend.tf   # reference copies — see note below
│   ├── platform/                 # once per GCP project
│   ├── tenant/                   # once per tenant
│   └── tenants/example.tfvars    # committed template; real *.tfvars are gitignored
├── scripts/
│   ├── lib.sh                    # guard, logging, secret-input helpers
│   ├── provision.sh              # the 5 phases below, each callable alone
│   ├── destroy.sh                # tenant / platform / all teardown
│   ├── smoke_test.sh             # post-deploy checks (+ bucket isolation test)
│   ├── grant_tenant.py           # DB grants (see "Grants" below)
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
  the example).

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
  `terraform/tenant/main.tf` creates `slack-bot-token-<tenant>`,
  `slack-signing-secret-<tenant>`, and `gemini-api-key-<tenant>` as empty
  Secret Manager shells (no `google_secret_manager_secret_version` at all).
  Values are added later by `scripts/provision.sh`'s `slack-secrets` phase
  directly via `gcloud secrets versions add`, using `printf '%s'` piped to
  `--data-file=-` — **never** `echo` or a here-string, because either one
  appends a trailing newline that silently breaks the Slack signing-secret
  HMAC check (this happened before in the manual deployment).
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

What was **written but not run against real infrastructure**: every
`terraform apply`/`destroy` against actual GCP resources, `gcloud builds
submit`, the Slack app creation/install flow, the frontend build+Hosting
deploy, and `smoke_test.sh`'s HTTP checks. Hoàng will do the real end-to-end
test on a fresh GCP project.

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
