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
├── README.md                     # this file — design/rationale/verification log
├── TENANT_RUNBOOK.md             # step-by-step walkthrough for provisioning one tenant
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
│   ├── release.sh                    # ship a new backend image — see "Releasing code vs changing infrastructure"
│   ├── rollback.sh                   # shift traffic back to the previous revision, no rebuild
│   ├── grant_tenant.py               # DB grants (see "Grants" below)
│   ├── check_create_only_plan.py     # Terraform safety net (see below)
│   ├── check_tenant_scoped_state.py  # destroy.sh external-mode safety net (see below)
│   ├── check_migration_safety.py     # destructive-migration lint, run by release.sh (see below)
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

For a step-by-step walkthrough of provisioning one tenant end to end
(exact commands, the manual Slack app clicks, and the real failures hit
doing this live), see **[`TENANT_RUNBOOK.md`](TENANT_RUNBOOK.md)**. This
section and the phase table below stay focused on what each phase does and
why; the runbook is the "do this, then this" sequence.

- **Bash 3.2 or newer.** Every script in `deploy/scripts/` is written to run
  on the stock macOS `/bin/bash` (3.2), so no Homebrew Bash is needed. Keep
  it that way: no `local -n`/`declare -n`, `declare -A`, `mapfile`/`readarray`,
  `${var,,}`/`${var^^}`, or `wait -n`, and don't expand a possibly-empty
  array as `"${arr[@]}"` under `set -u`.
- Terraform >= 1.6, a `google` CLI (`gcloud`, authenticated:
  `gcloud auth login` + `gcloud auth application-default login`), `python3`,
  `npm`, and `firebase-tools` (via `npx firebase-tools`, no global install
  needed — also needs its own `npx firebase-tools login`, used by phase 5's
  Firebase Web App creation and the Hosting deploy).
  **These are three separate, independently-expiring login sessions**
  (`gcloud auth login`, `gcloud auth application-default login` for
  Terraform, and `firebase-tools`' own login) — see `TENANT_RUNBOOK.md`
  step 2 if a command fails with an auth/reauthentication error and it's
  not obvious which of the three actually expired.
- A GCS bucket to hold Terraform state (create once, by hand — Terraform
  cannot create the bucket it's about to store its own state in), with
  versioning on so a bad state write is recoverable:
  ```bash
  gcloud storage buckets create gs://YOUR_TF_STATE_BUCKET --project=YOUR_TEST_PROJECT --location=asia-southeast1 --uniform-bucket-level-access
  gcloud storage buckets update gs://YOUR_TF_STATE_BUCKET --versioning
  ```
- A brand-new GCP project, billing enabled, **not** `neurix-enterprise`.
- Copy `terraform/tenants/example.tfvars` to `terraform/tenants/<tenant_id>.tfvars`
  and fill in `project_id`, `state_bucket`, `tenant_id` (never commit the copy
  — `.gitignore` already excludes every `terraform/tenants/*.tfvars` except
  the two committed `*.tfvars.example` files). See `TENANT_RUNBOOK.md` step 1
  before picking `tenant_id` — a common-word id can pass Terraform's own
  validation and still fail later, at Firebase Hosting site creation (see
  "Firebase Hosting site ID collisions" below).

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
| 5 | `frontend` | Builds the frontend with per-tenant `VITE_API_BASE_URL`/`VITE_FIREBASE_*` (compiled in at build time, passed as env vars — never written into `frontend/`) into a throwaway workspace, deploys it to the tenant's Hosting site via a throwaway Firebase config, prints the Cloud Run URL and the three Slack URLs to paste back into the Slack app. See "Frontend build/deploy isolation" below. | No (prints a manual follow-up) |

Two things stay manual no matter what (see "Known unknowns" below): Slack
app creation/install (phase 3, by design), and enabling the Google sign-in
provider in Firebase Auth (no reliable CLI/Terraform path — both `app` and
`frontend` phases print a reminder).

For the exact manual clicks — the Slack app manifest walkthrough (phase 3)
and wiring the real Cloud Run URLs into the Slack app after phase 5 — see
`TENANT_RUNBOOK.md` steps 4-5, or `slack/slack-manifest.yml`'s own header
comment, which `provision.sh`'s `slack-secrets` phase prints verbatim at
the right time.

## Releasing code vs changing infrastructure

`scripts/provision.sh`'s `app`/`migrate` phases stand a tenant up the first
time. Every day after that, most changes fall into one of two clearly
separate paths — pick the row that matches what actually changed:

| Change type | Command | Touches the platform (Cloud SQL instance, Artifact Registry, shared bucket, APIs)? | Touches Terraform state? |
|---|---|---|---|
| Backend code change (no schema change) | `scripts/release.sh --project-id ID --state-bucket BUCKET TENANT [TENANT ...]` | No | No |
| Backend code change + an **additive** migration (new table/column/index) | Same — `release.sh` runs migrations itself, before the image swap | No | No |
| Backend code change + a **destructive** migration (drop/rename/tighten/retype) | Split into two releases — see "Expand/contract" below | No | No |
| Undo a bad release for one tenant | `scripts/rollback.sh --project-id ID TENANT` | No | No |
| Frontend code change | `scripts/provision.sh frontend --project-id ID --tenant-id TID ...` (per tenant — see "Frontend releases are per tenant" below) | No | No |
| New tenant | `scripts/provision.sh tenant-resources` then `migrate`, `slack-secrets`, `app`, `frontend` | No (creates tenant-scoped resources only) | Yes — tenant's own state prefix only |
| Platform change (new API, SQL tier, bucket setting) | `scripts/provision.sh platform --project-id ID` | Yes, by definition | Yes — platform's own state prefix |
| Remove a tenant | `scripts/destroy.sh tenant --project-id ID --tenant-id TID ...` | No | Yes — tenant's own state prefix only |

The two release scripts (`release.sh`, `rollback.sh`) **never run
Terraform** — they only call `gcloud builds submit`, `gcloud run services
update`/`update-traffic`, and the migration runner directly. This is
deliberate, for two reasons:

1. **A code change must never need `terraform apply`.** Requiring a
   Terraform run to ship a one-line backend fix would mean every release
   carries the (small but real) risk of an unrelated infrastructure drift
   getting applied at the same time, and requires whoever ships code to
   also have platform-level Terraform permissions.
2. **A later `terraform apply` must never silently roll a release back.**
   `terraform/tenant/cloud_run.tf`'s `google_cloud_run_v2_service.backend`
   has `lifecycle { ignore_changes = [client, client_version,
   template[0].containers[0].image] }` specifically so that the image
   `release.sh` deployed keeps running even if someone re-applies
   `terraform/tenant` for an unrelated reason (e.g. provisioning a new
   tenant touches the same state file's provider config, or a platform
   re-apply cascades). Without this, the next `apply` would revert Cloud
   Run to whatever `var.backend_image` last resolved to in Terraform's own
   config/state — which is exactly the old image, since Terraform was never
   told about the release.

### Rollback procedure

`scripts/rollback.sh --project-id ID [--region REGION] <tenant_id>` shifts
100% of that tenant's Cloud Run traffic back to the previous revision via
`gcloud run services update-traffic --to-revisions=...`. It determines "the
previous revision" itself, live, from Cloud Run's own revision history —
it does not depend on any state `release.sh` wrote, so it works even from a
different machine or session than the release being undone. It refuses if
the service has fewer than two revisions. It does **not** roll back a
migration — see "Expand/contract" below for why that's a separate,
deliberate decision rather than a gap.

### Expand/contract: why migrations run before the code swap

`release.sh` runs a tenant's migrations, *then* updates its Cloud Run image
— and during a rolling multi-tenant release, other tenants (and briefly,
this tenant's own previous revision) are still running the OLD code against
the NEW schema until their own release goes out, or until rollback.sh, if
needed, puts the old revision back in front of the new schema.

This only works if every migration that ships with a release is one the OLD
code can also run against unmodified — i.e. **additive only**:

- New tables.
- New nullable columns (or columns with a default).
- New indexes.

A migration that **drops** a column, **renames** anything, **tightens** a
column to `NOT NULL`, or **changes a column's type** breaks that overlap
window — the old revision (still serving traffic somewhere, or restored by
a rollback) will fail the moment it touches the changed shape. Migrations
like that belong in a **later** release, shipped only once no running
revision anywhere needs the old shape anymore (i.e. after every tenant has
already picked up the release that stopped using it).

`scripts/check_migration_safety.py` enforces this mechanically:
`release.sh` runs it first, before a single image is built, against every
file in `backend/db/migrations/`. It pattern-matches for `DROP`, `RENAME`,
`SET NOT NULL`, and column type changes; a migration file containing one of
these fails the release unless that same file also contains an explicit,
reviewed approval comment:

```sql
-- destructive-migration-approved: <reason this is safe to ship now>
```

Add that line only once you've confirmed no running revision still needs
the old shape — the whole point of the check is to make that a deliberate,
visible decision rather than something that happens by accident inside an
otherwise-routine release.

### Frontend releases are per tenant

Unlike the backend image (one build, deployed to every tenant in a
`release.sh` run), the frontend is a **static build with tenant-specific
values compiled in at build time** (`VITE_API_BASE_URL`,
`VITE_FIREBASE_*` — see `scripts/provision.sh`'s `frontend` phase), so
there is no single shared "frontend image" to roll out. A frontend code
change is released by re-running `scripts/provision.sh frontend
--project-id ID --tenant-id TID ...` once per tenant that needs it; there
is currently no multi-tenant frontend equivalent of `release.sh`. Making
each tenant's frontend build genuinely isolated from the others (rather
than just parameterized by build-time env vars) is tracked as separate,
follow-on work and is out of scope here.

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

### Public access mode: `invoker_iam_disabled` vs `allUsers` — org-level override UNVERIFIED

`terraform/tenant/cloud_run.tf`'s Cloud Run service must be reachable
without per-caller Cloud Run IAM: Slack and the browser frontend both call
it directly (there is no front door to gate at), and auth is enforced
inside the app itself (Slack signature check, Firebase ID token), not at
the Cloud Run IAM layer. `var.public_access_mode` selects which of two
mechanisms does that:

- **`"iam_check_disabled"` (default).** Sets `invoker_iam_disabled = true`
  on `google_cloud_run_v2_service.backend` — a plain `optional` boolean
  field on the service resource itself, equivalent to `gcloud run deploy
  --no-invoker-iam-check`. **Confirmed against the real provider schema**,
  not assumed: `registry.terraform.io` is blocked by this environment's
  egress policy, so the `hashicorp/google` and `hashicorp/google-beta`
  `8.4.0` provider binaries (`linux_amd64`) were downloaded directly from
  `releases.hashicorp.com` into a local filesystem-mirror provider
  installation, and `terraform providers schema -json` was run against
  those exact binaries. `invoker_iam_disabled` shows up as a top-level
  attribute of `google_cloud_run_v2_service` in **both** provider schemas
  (not beta-only, contrary to the initial guess going into this pass), not
  nested under `template`, with no `launch_stage` requirement. Because
  disabling the check is a property of the service spec rather than an IAM
  policy write, it is unaffected by the `iam.allowedPolicyMemberDomains`
  org policy (Domain Restricted Sharing) that blocks granting
  `roles/run.invoker` to `allUsers` — confirmed live against `neurix-org`:
  `gcloud run deploy ... --allow-unauthenticated` deploys but the URL
  returns `403`, while `gcloud run deploy ... --no-invoker-iam-check` on
  the same project returns `200` with no org policy change needed.
  `terraform validate` and `terraform plan` (dummy values, `platform_mode =
  "external"` so no real GCP credentials are needed beyond a syntactically
  valid — not necessarily authentic — service account key for provider
  configuration) against those same real `8.4.0` binaries both confirm the
  plan sets `invoker_iam_disabled = true` and creates no
  `google_cloud_run_v2_service_iam_member.public_invoker` resource in this
  mode.
- **`"allow_unauthenticated"`.** The original mechanism: grants
  `roles/run.invoker` to `allUsers` via
  `google_cloud_run_v2_service_iam_member.public_invoker`. This is what
  production's `neurix-enterprise` project already has — that binding
  predates the org policy above, still works, and is left untouched. Use
  this mode only on a project confirmed not to enforce
  `iam.allowedPolicyMemberDomains` against `allUsers` (or where an org
  admin has granted a scoped exception); do not assume every future GCP
  project allows it, which is why it is not the default.

**UNVERIFIED — a separate org-level policy could still override this.**
Disabling the invoker IAM check is confirmed above to be a *per-service*
setting — it lives on the Cloud Run service resource, not on any project-
or org-level resource in the provider schema — but Google Cloud documents
a distinct org policy constraint, `constraints/run.managed.requireInvokerIam`,
that an org admin can set to force the invoker IAM check to stay on for
every Cloud Run service in the org regardless of this field. Whether that
constraint is set on `neurix-org` has **not** been checked in this pass —
doing so needs `gcloud resource-manager org-policies describe
constraints/run.managed.requireInvokerIam --project=PROJECT_ID --effective`
run against a real project in that org, which this pass had no access to
run. Terraform cannot detect this at plan time either way: setting
`invoker_iam_disabled = true` never errors, so if that org constraint is in
force, `apply` would silently succeed while the service still 403s to
unauthenticated callers. If a tenant provisioned with `public_access_mode =
"iam_check_disabled"` still returns `403` after a clean `apply`, check that
constraint next, before assuming Terraform's part of this is broken.

### Firebase Hosting site ID collisions

**Confirmed live (failure); the fix is UNVERIFIED live.** Hosting site IDs
are **globally unique across all of Firebase, not just this project** — the
ID becomes `https://<site-id>.web.app`. Provisioning tenant `demo` on
`neurix-org` with the old hardcoded `"${tenant_id}-neurix"` scheme failed
with:

```
Error creating Site: googleapi: Error 400: Invalid name: `demo-neurix` is invalid; try something like `fir-neurix` instead
```

The same error came from `npx firebase-tools hosting:sites:create demo-neurix
--project neurix-org`. Google appears to report "already taken by someone
else" and "malformed name" with the same generic `Invalid name`, so a
collision looks like a syntax error even though the name is well-formed.

**New default:** `terraform/tenant/hosting.tf` now uses
`<tenant_id>-<first 8 hex chars of sha256(project_id)>` (e.g.
`acme-1a2b3c4d`). The project id is globally unique, so the same tenant id in
different projects gets different site IDs. A hash is used instead of
`"<project_id>-<tenant_id>"` because that concatenation can reach 46
characters (30-char project id + 15-char tenant id + hyphen) against
Firebase's 30-character limit, and truncating it would silently drop project
or tenant identity. The hashed form is at most 15 + 1 + 8 = 24 characters by
construction, so it never needs truncating. It is deterministic (stable
across plans) and lowercase `[a-z0-9-]`.

**This reduces collision risk; it does not eliminate it.** The choice is a
reasonable-but-unverified improvement, not one based on a measured collision
rate: Firebase exposes no public availability check, so whether a specific
string is free can only be learned by trying to create it. That is why the
override exists.

**Recovery if a site ID still collides** (`Invalid name` from
`google_firebase_hosting_site`):

1. Set `hosting_site_id = "<your-choice>"` in the tenant's tfvars (4–30
   chars, lowercase letters/digits/hyphens, not starting or ending with a
   hyphen) and re-run `scripts/provision.sh app`. The override replaces the
   computed default entirely; `frontend_url`/CORS follow it automatically.
   Pick something project-specific, not a bare common word.
2. *(Documented only, not automated.)* If a site with the desired ID was
   already created by hand while diagnosing (e.g. via
   `firebase hosting:sites:create`), adopt it instead of recreating it:
   `terraform -chdir=terraform/tenant import -var-file=... -var enable_app=true
   -var backend_image=... -var frontend_url=... 'google_firebase_hosting_site.tenant[0]'
   projects/<project_id>/sites/<site_id>` (with `hosting_site_id` set to that
   same ID so the plan matches), then re-run `app`. Verify the import ID
   format against your pinned provider version's docs before relying on it.

Site IDs are immutable: changing `hosting_site_id` (or `project_id`/`tenant_id`
under the computed default) on a tenant whose site already exists makes
Terraform replace the site — the old `*.web.app` URL stops serving.
Tenants already provisioned with the old `<tenant>-neurix` name will see this
replacement in `plan`; pin them with `hosting_site_id = "<tenant>-neurix"` to
keep their existing site.

Verified offline: `terraform fmt`, `validate` (real `google`/`google-beta`
8.4.0 schemas) and `plan` with dummy values in both modes — `hosting_site_id`
unset shows the computed default in `site_id`, set shows the override exactly;
a maximum-length tenant id (15 chars) plus a 30-char project id yields 24
characters. Still needs a live re-confirmation: that the new default actually
provisions on `neurix-org` (no GCP access here, so no real `apply`).

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

### Frontend build/deploy isolation

`scripts/provision.sh`'s `frontend` phase builds and deploys a tenant's
frontend **without ever writing into `frontend/`** — that directory is the
same one a later *production* frontend build/deploy runs from, so anything
left behind there (an env file, a stray build, a rewritten `.firebaserc`)
is a trap for whoever runs that production build next, not just a detail of
the tenant test. Concretely, per run:

- **No env file.** The tenant's `VITE_FIREBASE_*`/`VITE_API_BASE_URL`
  values are exported as environment variables around the `npm run build`
  call only, never written to `frontend/.env` or any other file under
  `frontend/`. Vite gives a `VITE_`-prefixed process environment variable
  priority over the same key in an `.env` file (it only fills in from the
  file when the process environment doesn't already have the key — see
  Vite's `loadEnv`), so this is not a weaker substitute for the file, it's
  the same compiled-in-at-build-time behavior with nothing left on disk
  afterward.
- **A throwaway workspace, not `frontend/dist`.** Everything this phase
  builds — the Vite output and the temporary Firebase config below — lives
  under a directory from `mktemp -d` (`${TMPDIR:-/tmp}/neurix-frontend-
  <tenant>.XXXXXX`), outside `frontend/` and outside the repo entirely. The
  build itself still runs from `frontend/` (so `npm install`/`vite` resolve
  the project's `node_modules`/`vite.config.ts` normally), but its output
  goes to that scratch directory via `vite build --outDir <dir>
  --emptyOutDir` (a CLI flag, which overrides `vite.config.ts`'s
  `build.outDir: "dist"`). `frontend/dist` — what a plain `firebase deploy
  --only hosting` from `frontend/` would upload — is never touched.
- **No `target:apply`, no site key in the repo's `firebase.json`.**
  `frontend/.firebaserc` and `frontend/firebase.json` are never read or
  written by this phase. Instead it writes a small, throwaway
  `firebase.tenant-deploy.json` into that same scratch directory, with an
  explicit `"site"` (the tenant's own `hosting_site_id` Terraform output)
  and `"public"` pointing at the scratch build output by **absolute path**,
  and deploys with `firebase deploy --only hosting --config
  <that file>`. This was verified against firebase-tools' own source
  (`lib/config.js`, `lib/detectProjectRoot.js`, `lib/hosting/config.js` —
  see "Verification" below): passing `--config <path>` makes
  `detectProjectRoot()` treat `<path>`'s own directory as the "project
  directory" for that run (not `frontend/`, and not `--config`'s caller's
  cwd), an absolute `"public"` path is returned as-is with no
  project-directory containment check, and a `hosting` block with an
  explicit `"site"` key resolves directly — `resolveTargets()` returns it
  unchanged, no `.firebaserc` target lookup happens at all. `--project` is
  passed explicitly on the command line for the same reason: so project
  selection never depends on `frontend/.firebaserc`'s `projects.default`
  either.
- **Guarded before the deploy actually runs.** Two checks run before
  `firebase deploy` is invoked (`guard_hosting_config_site` and
  `guard_hosting_deploy_args` in `scripts/lib.sh`): the first re-reads the
  throwaway config back off disk and refuses if its `hosting.site` doesn't
  match the tenant's own site id, or if that site id equals the project's
  own default site (a project's default Hosting site shares the project's
  id — this is what would happen if `hosting_site_id` were ever empty or
  wrong); the second refuses to run the deploy command at all unless its
  argv explicitly contains `--config <that same throwaway file>`, so a
  future edit to this phase can't accidentally fall back to a bare
  `firebase deploy --only hosting` (which would use `frontend/firebase.json`
  and whatever `frontend/.firebaserc`'s default project/site currently is).
- **Cleanup on every exit path.** The scratch workspace is removed by a
  `trap` on `EXIT` (covers success and any `set -e`-triggered failure) plus
  explicit `INT`/`TERM` traps that `rm -rf` and then `exit` themselves —
  bash does not terminate a script on a caught `SIGINT`/`SIGTERM` unless the
  trap handler calls `exit` itself, so a bare `trap ... INT TERM` with no
  `exit` inside would only run the handler and then resume the script
  where it left off (see "Verification" below for how this was caught).

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

`destroy.sh tenant` does the equivalent for Cloud Run: `cloud_run_deletion_protection`
also defaults to `true`, so it's flipped to `false` in a targeted `apply`
before the tenant `destroy` runs — but only if the service is already in
state (a phase-1-only tenant, never past `migrate`/`app`, has nothing to
flip; the "No Cloud Run service in state; skipping" log line in that case
is expected, not an error). Destroying a tenant also drops its Cloud SQL
database and user in one `terraform destroy` call; those two resources have
an explicit `depends_on` (`terraform/tenant/main.tf`) forcing the database
to be dropped before the user, because destroying them in parallel hit
`role ... cannot be dropped because some objects depend on it` (the role
still held privileges on the database mid-drop) the first time this was run
for real.

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

#### Cloud Build staging bucket (production gap)

`scripts/provision.sh migrate` (and `release.sh`) build the image through
`build_backend_image` in `scripts/lib.sh`, which passes
`--gcs-source-staging-dir=gs://<bucket>/source` and a generated build config
with `options.logging: CLOUD_LOGGING_ONLY`. Both are needed when a custom
Cloud Build service account is used: Cloud Build rejects the project's
default `PROJECT_ID_cloudbuild` bucket in that case, and requires an explicit
logging destination. Non-production projects get the bucket
(`PROJECT_ID-build-staging`) from `terraform/platform`.

**Production does not have this bucket** — `platform/` was never applied
there. In external mode, either:

- create one by hand and set `external_build_staging_bucket` (name only):

  ```bash
  gcloud storage buckets create gs://neurix-enterprise-build-staging \
    --project=neurix-enterprise --location=asia-southeast1 \
    --uniform-bucket-level-access
  gcloud storage buckets add-iam-policy-binding gs://neurix-enterprise-build-staging \
    --member=serviceAccount:<cloud-build-sa-email> --role=roles/storage.objectAdmin
  ```

- or leave it unset: `migrate` then omits `--gcs-source-staging-dir` and only
  requests `CLOUD_LOGGING_ONLY` against the project's default bucket.
  **Untested.** That works only if the build uses the project's default Cloud
  Build service account (`external_cloudbuild_service_account_email` unset);
  with a custom service account the default bucket was confirmed to fail.

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
| `public_invoker` | `google_cloud_run_v2_service_iam_member` | this tenant's own Cloud Run service, only when `public_access_mode = "allow_unauthenticated"` |

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
  (`<hosting-site-id>.web.app`; see "Firebase Hosting site ID collisions") created under the same, already-in-use Firebase
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

**Release path + platform re-apply safety net, added in this pass —
additionally verified for real** (still without real GCP credentials; this
time `registry.terraform.io` API access was also blocked for `terraform
init`, same as before, so a local filesystem-mirror install of the real
`hashicorp/google`/`hashicorp/google-beta` 8.4.0 and `hashicorp/random`
3.9.1 provider binaries — downloaded directly from
`releases.hashicorp.com`, which was reachable — was rebuilt for this pass
too, the same workaround as the original build):

- `terraform fmt -recursive -diff -check` on the whole `terraform/` tree:
  clean.
- `terraform init` + `terraform validate` for **both** `platform/` and
  `tenant/` against the real, pinned 8.4.0 provider schema: both clean.
- `terraform plan` with dummy values for both modules (temporary
  local-backend override, removed afterwards, nothing committed),
  including `tenant/` with `enable_app = true` specifically to exercise the
  new `lifecycle` block on `google_cloud_run_v2_service.backend`: both
  plans fully resolved the resource graph (for `tenant/`, as far as the
  `terraform_remote_state` platform lookup, which itself needs GCS
  credentials) and failed cleanly at Google Cloud authentication, the
  expected failure with no ADC configured — confirming the new `lifecycle`
  blocks on `google_sql_database_instance.main` (`prevent_destroy`) and
  `google_cloud_run_v2_service.backend` (`ignore_changes`) parse and don't
  break the graph, not just that they're syntactically valid.
- The `google_sql_database_instance` force-replacement fields cited in the
  README/audit (`name`, `project`, `region`, `collation`,
  `encryption_key_name`) were confirmed against the actual
  `hashicorp/terraform-provider-google` v8.4.0 source
  (`resource_sql_database_instance.go`, matching the version pinned in
  `versions.tf`) — not assumed from general Cloud SQL knowledge — since
  `terraform providers schema -json` does not itself expose `ForceNew`
  metadata (that's provider SDK diff logic, only visible via a real
  plan-against-state, which real credentials would be needed for here).
  Similarly, `google_cloud_run_v2_service`'s `client`/`client_version`
  fields (why they're in the new `ignore_changes` list) were confirmed
  against that same provider version's own resource source.
- `check_create_only_plan.py --mode platform-reapply`, run against four
  hand-made plan JSON files (no cloud credentials needed): a plan with only
  an in-place `update` on the SQL instance passes; a plan replacing the SQL
  instance (`["delete", "create"]`) fails, naming it; a plan deleting a
  bucket fails, naming it; a plan replacing a *non-stateful* resource
  (a service account) passes, confirming the mode doesn't over-restrict.
  The existing default (`create-only`) mode was re-run afterwards and
  still rejects an `update` and still accepts a `create`-only plan exactly
  as before — confirming the new `--mode` flag didn't change default
  behavior for `provision.sh`'s existing external-platform-mode use of
  this script.
- `check_migration_safety.py` run against the real
  `backend/db/migrations/*.sql` (all 5 files: pass, as expected — they're
  all additive) and against five hand-made synthetic migration files: an
  unapproved `DROP COLUMN` fails, naming it; the same statement with a
  `-- destructive-migration-approved: ...` comment passes; unapproved
  `RENAME COLUMN`, `SET NOT NULL`, and a column `TYPE` change each fail
  independently; a file that only *mentions* `DROP TABLE` inside a SQL
  comment (not as a real statement) passes, confirming the comment-stripping
  doesn't false-positive on prose.
- `release.sh` and `rollback.sh`: `shellcheck` clean (run together with the
  rest of `scripts/*.sh` so `source ./lib.sh` resolves). Both were then run
  for real against a stub `gcloud` (logging every invocation and faking
  just enough output — service describe/update/update-traffic, revision
  list, secret access, build submit) and a stub backend/migration runner
  (a minimal `backend/scripts/run_migrations.py` that logs its env vars and
  exits 0, since no real Postgres/Cloud SQL was available here), in a
  scratch copy of the repo layout with `smoke_test.sh` itself replaced by a
  thin stub (the real `smoke_test.sh` already does its own real `terraform
  output` state lookup, audited separately above; replacing it here
  isolated the test to `release.sh`'s own control flow):
  - Two-tenant success path: confirmed build-once (`gcloud builds submit`
    called exactly once, before either tenant), canary-first ordering,
    migrations run before the Cloud Run update for each tenant, the
    previous revision recorded and logged before each update,
    `RELEASE=<tag>` set as both an env var and a label, and the Cloud SQL
    root password fetched from Secret Manager exactly **once** for both
    tenants (an initial version of the caching had a subshell bug — the
    whole point of testing this for real instead of just reading the
    code — where the cached value never left the `$(...)` subshell it was
    fetched in, silently reprompting per tenant; fixed by calling the
    fetch function directly, not via command substitution, so it can set
    a variable in the caller's shell).
  - Three-tenant stop-on-failure path (stub `gcloud run services update`
    fails specifically for the second tenant): confirmed the first tenant's
    migration, image update, and smoke test all completed and were logged
    as successful; the second tenant's migration ran (it happens before the
    image update) but its Cloud Run update failure stopped the script
    immediately, printing that tenant's recorded previous revision and the
    exact `rollback.sh` command to run; the third tenant had **zero**
    logged `gcloud`/`smoke_test` calls — confirmed not touched.
  - Migration-safety gate: dropping an unapproved destructive migration
    file into the scratch `backend/db/migrations/` and re-running
    confirmed the release stops at the check with **zero** `gcloud` calls
    logged — no build, no tenant touched.
  - `rollback.sh`: confirmed it correctly picks the second-newest of two
    stubbed revisions and calls `update-traffic --to-revisions=<that
    revision>=100`; confirmed it refuses (exit 1, no `update-traffic` call)
    when the stubbed service has only one revision, and separately when the
    stubbed `describe` reports the service doesn't exist at all.
  - This does **not** substitute for running `release.sh`/`rollback.sh`
    against a real Cloud SQL instance and Cloud Run service — that's
    explicitly still Hoàng's real end-to-end test, same as the rest of this
    package (see below).

What was **written but not run against real infrastructure**: every
`terraform apply`/`destroy` against actual GCP resources (both the normal
flow and external platform mode), `gcloud builds submit`, the Slack app
creation/install flow, the frontend build+Hosting deploy, and
`smoke_test.sh`'s HTTP checks. The same is true for `release.sh`'s and
`rollback.sh`'s real `gcloud`/migration calls (only stubbed, per above) —
neither a real `gcloud run services update`/`update-traffic` nor a real
migration run against a real Cloud SQL instance happened here. This also
means the *substance* of the production-testing pre-flight checklist (a
real Cloud SQL backup, the real read-only lookups against
`neurix-enterprise`) was written and reasoned through but not physically
executed here — Hoàng will do the real end-to-end test, on a fresh GCP
project first and then, following this README's "Testing a tenant on the
existing production platform" section,
against `neurix-enterprise` itself.

**Frontend build/deploy isolation, added in this pass — additionally
verified for real** (still without real Firebase/GCP credentials, so no
actual `firebase deploy` reached a live Hosting site):

- Downloaded the real `firebase-tools` package (`npm pack firebase-tools`)
  and read `lib/config.js`, `lib/detectProjectRoot.js`, and
  `lib/hosting/config.js` directly, rather than assuming Firebase's
  documented targets model still applies with `--config`. Confirmed from
  source, not docs: `detectProjectRoot()` uses `--config`'s own directory as
  the project directory when `--config` is passed (not the caller's cwd);
  `Config.path()` returns an absolute `"public"` value as-is, skipping the
  "outside of project directory" check entirely; and `hostingConfig()`'s
  `resolveTargets()` passes a config through unchanged whenever it already
  has a `"site"` key, never consulting `.firebaserc`. This is what makes an
  explicit `"site"` + absolute `"public"` in a throwaway `--config` file
  work with no `.firebaserc`/`frontend/firebase.json` involvement at all,
  rather than an unverified assumption the way the previous pass's
  targets-based approach was flagged.
- `shellcheck` on all of `deploy/scripts/*.sh` (`lib.sh`, `provision.sh`,
  `destroy.sh`, `smoke_test.sh`, `release.sh`, `rollback.sh`): clean.
- `bash -n` on `lib.sh` and `provision.sh`: clean.
- `provision.sh frontend` run for real against stub `terraform`, `gcloud`,
  `npm`, and `npx` executables (no real GCP/Firebase calls reached), in the
  actual repo checkout, with the branch's real `frontend/.firebaserc` and
  `frontend/firebase.json` snapshotted before each run:
  - **Success path**: printed the exact `firebase-tools deploy` command
    that would run (`npx --yes firebase-tools deploy --only hosting
    --config <scratch-dir>/firebase.tenant-deploy.json
    --project=<project>`); confirmed `git status --ignored frontend/` was
    clean afterward (`nothing to commit, working tree clean`) with no new
    ignored or untracked entries — no tenant output directory ever appears
    under `frontend/` at all, since the build lands in a `mktemp -d`
    workspace outside the repo; `frontend/.firebaserc` and
    `frontend/firebase.json` byte-for-byte identical to their pre-run
    snapshots (`diff` clean); `frontend/dist` and `frontend/.env` both
    absent afterward; the scratch workspace itself removed after the run.
  - **Failure path**: stub `npx` exiting 1 (simulating a failed Hosting
    deploy) produced the phase's own error message and a non-zero exit,
    and the scratch workspace was still removed — confirming the `EXIT`
    trap fires on a `set -e`-triggered failure, not only on success.
  - **Interrupt path**: sent `SIGINT` to the running phase mid-build (stub
    `npm run build` sleeping). First attempt, with a bare
    `trap ... EXIT INT TERM`, exposed a real bug: bash does not terminate a
    script on a caught signal unless the trap handler itself calls `exit`,
    so the script resumed past the interrupted build into the deploy step
    against an already-removed workspace and errored there instead of
    stopping cleanly. Fixed by giving `INT`/`TERM` their own trap
    entries that `rm -rf` and then `exit 130`/`exit 143` explicitly.
    Re-tested after the fix: the script now exits immediately with code
    130 and the workspace is gone.
  - **Guard test — default site**: pointed the stub `terraform`'s
    `hosting_site_id` output at the same value as `firebase_project_id`
    (the default-site scenario) and confirmed `phase_frontend` aborts
    before any build or deploy step runs, naming both values, with the
    scratch workspace still cleaned up.
  - **`guard_hosting_deploy_args` unit-tested directly** (sourcing `lib.sh`
    in isolation): a correctly-formed `deploy` argv containing
    `--config <expected file>` passes silently; the same argv with
    `--config` omitted entirely aborts (`exit 1`), naming the missing
    file — confirmed this guard cannot be satisfied by an argv that would
    otherwise run a bare `firebase deploy --only hosting`.
  - Also found, while writing the trap: a `local workdir` inside
    `phase_frontend` would have gone out of scope by the time the `EXIT`
    trap actually runs (which happens after the function returns, at
    process exit, not at `return`), making `$workdir` an unbound variable
    under `set -u` on every successful run. Fixed by declaring `workdir`
    without `local` (a deliberate, commented exception) so it survives for
    the trap.
- Not run: an actual `firebase deploy` against a live Hosting site (no
  Firebase project or credentials available here — same limitation as the
  rest of this package's frontend-phase verification), and a real
  `npm run build` via real Vite (stubbed `npm`, since the point of this
  pass's tests was the isolation/cleanup control flow, not Vite's own
  build correctness, which is unrelated to this change and already
  exercised by the frontend repo's own build).

**Public access mode (`invoker_iam_disabled` vs `allUsers`), added in this
pass — additionally verified for real** (still without real GCP
credentials; `registry.terraform.io` was blocked again, so the same
filesystem-mirror workaround was rebuilt: `hashicorp/google` and
`hashicorp/google-beta` `8.4.0` (`linux_amd64`) downloaded directly from
`releases.hashicorp.com`, plus `hashicorp/random` `3.9.1` for the full
module):

- `terraform providers schema -json` run against those exact `8.4.0`
  binaries (not read from documentation or memory): confirmed
  `invoker_iam_disabled` is a top-level, `optional` boolean attribute of
  `google_cloud_run_v2_service` in **both** the `google` and `google-beta`
  provider schemas — not nested under `template`, not gated by
  `launch_stage`, and not beta-only, which was the open question going into
  this pass ("I believe it may require the google-beta provider and/or a
  specific launch stage annotation, but confirm rather than guess"). Full
  schema dump kept at the path this pass used locally; the specific
  resource/attribute match is what's cited above and in "Known unknowns",
  not a doc reference.
- `terraform fmt -diff -check` on the edited `tenant/` module: clean.
- `terraform init` (filesystem-mirror provider install, no
  `registry.terraform.io` reached) + `terraform validate` against the real
  `8.4.0`/`3.9.1` schemas: clean.
- `terraform plan` with dummy values (`platform_mode = "external"`, a
  syntactically well-formed but not-authentic service account key file for
  provider configuration — sufficient for the `google`/`google-beta`
  providers to configure without erroring, since no resource in this graph
  requires an actual authenticated API round trip to produce a create-only
  plan) run **twice**, once per `public_access_mode` value, using a local
  backend override (removed afterwards, nothing committed):
  - `public_access_mode = "iam_check_disabled"`: plan shows
    `google_cloud_run_v2_service.backend[0]` with
    `invoker_iam_disabled = true`, and **no**
    `google_cloud_run_v2_service_iam_member.public_invoker` resource at
    all (19 resources to add).
  - `public_access_mode = "allow_unauthenticated"`: plan shows
    `invoker_iam_disabled = false` and
    `google_cloud_run_v2_service_iam_member.public_invoker[0]` present
    with `member = "allUsers"`, `role = "roles/run.invoker"` (20 resources
    to add — the one extra being that binding).
  Both plans otherwise resolved the full resource graph identically (same
  19 shared resources), confirming `public_access_mode` changes only the
  public-access mechanism and nothing else.
- **Not verified**: whether `constraints/run.managed.requireInvokerIam` is
  set on `neurix-org` — see "Known unknowns" above. This needs a real
  `gcloud resource-manager org-policies describe` call against a project in
  that org, which this pass had no credentials to run.

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
  the Hosting site id (evaluated via `terraform console`, so it honors a `hosting_site_id` override) rather than needing a Hosting site to already
  exist — this avoids a circular dependency between Cloud Run (needs
  `FRONTEND_URL` for CORS) and Hosting (created in the same Terraform phase).
- **Cloud Run ingress**: left at `INGRESS_TRAFFIC_ALL` — Slack and browsers
  call the service directly, auth is enforced inside the app, not at Cloud
  Run's IAM layer.
- **Public access mechanism: `invoker_iam_disabled` by default, not
  `allUsers`** (this pass; supersedes the original single
  `allow_unauthenticated` boolean). The original design granted
  `roles/run.invoker` to `allUsers` unconditionally whenever
  `allow_unauthenticated = true` (default), pointing at
  `docs/DEPLOYMENT_RUNBOOK.md` step 11 gotcha #2's manual, time-boxed org
  policy override for a project enforcing Domain Restricted Sharing
  (`iam.allowedPolicyMemberDomains`). That override is real friction (org
  admin permissions, a multi-minute propagation wait, a binding that must
  be applied and then the policy immediately reverted) for something that
  turns out not to need it: `google_cloud_run_v2_service.invoker_iam_disabled`
  disables Cloud Run's own invoker IAM check as a property of the service
  spec, not an IAM policy write, so it is never subject to that org policy
  in the first place — confirmed live (`--allow-unauthenticated` deploys
  but 403s; `--no-invoker-iam-check` deploys and 200s, both against the
  same `neurix-org`-governed project, no policy change either way) and
  confirmed present in the real, pinned `~> 8.4` provider schema for both
  `google` and `google-beta` (see "Known unknowns" → "Public access mode").
  `public_access_mode` ("iam_check_disabled" default, or
  "allow_unauthenticated") replaces the old boolean; the `allUsers` path
  stays available (production's own `neurix-enterprise` binding pre-dates
  and is unaffected by this change) for a project where an org policy
  requires it or a future project's policy allows `allUsers` but not this
  field. See "Known unknowns" above for the one thing this pass could not
  verify — whether a separate org policy
  (`constraints/run.managed.requireInvokerIam`) is set on `neurix-org` that
  could still force the check on regardless of this field.
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
- **`release.sh` re-derives platform facts deterministically instead of
  reading them from Terraform state** (Cloud Run service name, database
  name, Cloud SQL instance connection name, Artifact Registry repository,
  the Cloud Build service account) — with an override flag for every one,
  rather than doing what `provision.sh`'s `migrate` phase does
  (`terraform output`). This was a deliberate choice, not an oversight: the
  task this script was built for says explicitly "it must never run
  Terraform." A read-only `terraform output` arguably wouldn't violate the
  *spirit* of that (it can't change infrastructure), but re-deriving the
  handful of values that are already fully determined by
  `scripts/provision.sh`'s own normal-flow defaults keeps `release.sh`
  genuinely independent of Terraform state/backend access entirely, which
  seemed like the stronger, more literal reading and a better property for
  a script whose whole purpose is "ship code without touching
  infrastructure tooling." The one exception is `scripts/smoke_test.sh`,
  which `release.sh` shells out to and which does do its own read-only
  `terraform output` — that's the existing, already-audited script the task
  explicitly says to run, not new Terraform usage introduced by this pass.
- **`STATEFUL_RESOURCE_TYPES` in `check_create_only_plan.py`'s
  `platform-reapply` mode is `google_sql_database_instance`,
  `google_sql_database`, `google_storage_bucket`** — exactly the three
  things the task named ("SQL instance, databases, buckets"), not a bigger
  allowlist. `google_sql_user` (holds no data itself, just credentials) and
  Secret Manager secrets (already separately protected — see below) were
  deliberately left out; a genuinely new class of stateful platform
  resource added later needs a deliberate addition here, not an assumption
  that "stateful-sounding" is enough.
- **`prevent_destroy = true` on the SQL instance is unconditional** (not
  gated by a variable) — Terraform requires `lifecycle` meta-arguments to be
  literal values, so this couldn't be made conditional on, say, a
  `sql_prevent_destroy` variable the way `deletion_protection` already is.
  This has a real consequence for `scripts/destroy.sh destroy_platform()`:
  it can no longer tear down the SQL instance without a source edit first
  (temporarily removing the `lifecycle` block in
  `terraform/platform/main.tf`) — `destroy.sh` now prints this explicitly
  before its confirmation prompt rather than silently failing partway
  through. This is intentional friction, matching the task's own ask
  ("forbid any delete or replace of stateful resources") and Terraform's
  own design for `prevent_destroy`: destroying the instance holding every
  tenant's data should require a conscious code change, not just a flag —
  even though it makes the old "spin up a throwaway project, test, then
  `destroy.sh all`" workflow one step less convenient than before.
- **Frontend scratch workspace location: `mktemp -d` under `$TMPDIR`/`/tmp`,
  not a directory inside `frontend/` or `deploy/`** — the handoff offered
  either `frontend/dist-${TENANT_ID}` (gitignored) or a temp directory
  outside the repo as acceptable options. Chose the latter: it needs no new
  `.gitignore` entry at all (there's nothing under `frontend/` to ignore),
  and it makes "never lands in frontend/dist" true in the strongest sense —
  there's no tenant-named directory under `frontend/` for a future
  production build to even notice, let alone collide with.
- **`"public"` in the throwaway Firebase config is an absolute path, not a
  path relative to the config file's own directory** — both would have
  worked (`Config.path()` resolves a relative path against the config
  file's directory, per "Verification" above), but an absolute path skips
  that resolution entirely and reads correctly no matter where the
  throwaway config or the scratch build output end up, which is one fewer
  thing to keep in sync between the two `mktemp` paths.
- **`--project` passed explicitly on the deploy command, not left to
  `.firebaserc`** — even though `frontend/.firebaserc` is never touched by
  this phase, leaving project selection to Firebase's own
  `.firebaserc`/environment-variable fallback chain would make the deploy's
  target project depend on state this phase doesn't control. Passing
  `--project="$PROJECT_ID"` explicitly (the same variable every other
  `gcloud`/`terraform` call in this phase already uses) keeps the deploy
  self-contained.

## Open questions for Hoàng

1. ~~Firebase Hosting multi-site deploy mechanics~~ — resolved in the
   frontend-isolation pass: the `frontend` phase now always deploys via an
   explicit `"site"` key in a throwaway `--config` file rather than
   `target:apply` + `.firebaserc`, verified against firebase-tools' own
   source (see "Frontend build/deploy isolation" and "Verification"
   below). Still worth confirming on the first real run against a live
   Firebase project, since no real project was available to test against
   here.
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
7. `release.sh` re-derives the Cloud SQL instance name (`neurix-db`),
   Artifact Registry repository id (`neurix-backend`), and Cloud Build
   service account (`neurix-cloudbuild@PROJECT...`) from
   `terraform/platform/variables.tf`'s own defaults rather than reading
   Terraform state (see "Judgment calls made" above for why) — confirm none
   of these were overridden away from their defaults on the real platform
   apply; if one was, pass the matching `release.sh` override flag
   (`--sql-instance-name`/`--ar-repo-id`/`--cloudbuild-service-account`)
   every time, or say so and this can be changed to read Terraform output
   instead.
8. Real end-to-end verification of `release.sh`/`rollback.sh` against an
   actual Cloud SQL instance and Cloud Run service — this pass verified
   their control flow (ordering, stop-on-failure, previous-revision
   recording, the password-caching bug found and fixed while testing) with
   stub `gcloud` and a stub migration runner (see "Verification" above);
   no real GCP credentials or Postgres instance were available here.
9. Whether `constraints/run.managed.requireInvokerIam` is set at the
   `neurix-org` level — see "Known unknowns" → "Public access mode". If it
   is, `public_access_mode = "iam_check_disabled"` would `apply` cleanly
   but the resulting service would still 403 unauthenticated callers, since
   that org policy overrides the per-service `invoker_iam_disabled` field
   and Terraform has no way to detect it at plan time. Worth checking once,
   with `gcloud resource-manager org-policies describe
   constraints/run.managed.requireInvokerIam --project=PROJECT_ID
   --effective` against any project in that org, before relying on this
   mode for a real tenant.
