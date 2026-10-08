#!/usr/bin/env bash
# Ships a new backend image to existing tenants WITHOUT touching Terraform
# or any infrastructure definition — see deploy/README.md "Releasing code
# vs changing infrastructure". This is the code-release path; provisioning
# a brand-new tenant, or changing anything about the platform stack, is
# still exclusively scripts/provision.sh's / terraform's job.
#
# This script's own logic never calls `terraform` (no plan/apply/destroy,
# not even a read-only `terraform output`): every fact IT needs directly
# (Cloud Run service name, database name, Cloud SQL instance connection
# name, Artifact Registry repository, the Cloud Build service account) is
# re-derived from the same deterministic naming conventions
# terraform/platform and terraform/tenant themselves use, with an override
# flag for every one of them in case a real deployment customized a
# platform default away from what scripts/provision.sh's normal flow
# produces. The one exception is step 3 below, which shells out to the
# existing scripts/smoke_test.sh — that script does its own read-only
# `terraform output` to discover a tenant's Cloud Run/Hosting URLs (see its
# own header: "Read-only — never modifies infrastructure"); release.sh
# still needs --state-bucket to pass through to it for that reason alone.
# This keeps "ship code" and "change infrastructure" on two genuinely
# independent code paths, per the task this script was written for: a code
# change must never need `terraform apply`, and a later `terraform apply`
# must never be able to silently roll a release back (see
# terraform/tenant/cloud_run.tf's `lifecycle { ignore_changes = [...] }` on
# the Cloud Run image, added alongside this script for exactly that reason).
#
# Usage:
#   release.sh --project-id ID --state-bucket BUCKET [--region REGION]
#              [--image-tag TAG] [--sql-instance-name NAME] [--ar-repo-id ID]
#              [--cloudbuild-service-account EMAIL] [--build-staging-bucket NAME]
#              [--sql-root-secret NAME]
#              [--tenants-file FILE] [TENANT ...]
#
# Tenants are rolled out ONE AT A TIME, IN THE ORDER GIVEN — the first
# tenant named (positionally, or the first line of --tenants-file) is the
# canary. Pass tenants either as positional arguments or one per line in
# --tenants-file (blank lines and lines starting with # are ignored); not
# both. For each tenant, in order:
#   1. Run backend/db/migrations against that tenant's database (same
#      scripts/run_migrations.py + guard_sql_target used by
#      scripts/provision.sh's migrate phase).
#   2. Update its Cloud Run service to the new image
#      (`gcloud run services update --image=...`), tagging the revision
#      with a RELEASE env var and a release= label set to the image tag.
#   3. Run scripts/smoke_test.sh against it.
# On the FIRST failure at any step, for any tenant, the script stops
# immediately — no later-named tenant is touched. The previous revision
# name for the tenant being rolled out when it stopped is printed (it was
# already recorded before that tenant's Cloud Run update ran), so rolling
# that one tenant back is one command: scripts/rollback.sh.
#
# The backend image is built and pushed exactly ONCE per release (before
# the per-tenant loop), tagged with an immutable tag: --image-tag if given,
# else the current git short SHA. The same image is then deployed to every
# tenant in this run.
#
# deploy/scripts/check_migration_safety.py runs first, before anything is
# built — see deploy/README.md's expand/contract rule. It scans every
# migration file for destructive statements (DROP/RENAME/SET NOT
# NULL/type change); an unapproved one stops the release before a single
# gcloud/docker command runs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
TF_DIR="$DEPLOY_DIR/terraform"

# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PROJECT_ID=""
STATE_BUCKET=""
REGION="asia-southeast1"
IMAGE_TAG=""
SQL_INSTANCE_NAME="neurix-db"          # must match terraform/platform/variables.tf's sql_instance_name default
AR_REPO_ID="neurix-backend"            # must match terraform/platform/variables.tf's artifact_registry_repository_id default
CLOUDBUILD_SA=""                       # default computed below once PROJECT_ID is known
BUILD_STAGING_BUCKET=""                # default computed below; must match terraform/platform/main.tf's build_staging bucket name
SQL_ROOT_SECRET="sql-root-password"    # must match terraform/platform/main.tf's google_secret_manager_secret.sql_root_password secret_id
TENANTS_FILE=""
TENANTS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --state-bucket) STATE_BUCKET="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --image-tag) IMAGE_TAG="$2"; shift 2 ;;
    --sql-instance-name) SQL_INSTANCE_NAME="$2"; shift 2 ;;
    --ar-repo-id) AR_REPO_ID="$2"; shift 2 ;;
    --cloudbuild-service-account) CLOUDBUILD_SA="$2"; shift 2 ;;
    --build-staging-bucket) BUILD_STAGING_BUCKET="$2"; shift 2 ;;
    --sql-root-secret) SQL_ROOT_SECRET="$2"; shift 2 ;;
    --tenants-file) TENANTS_FILE="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,62p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --) shift; break ;;
    -*) log_error "Unknown flag: $1"; exit 1 ;;
    *) TENANTS+=("$1"); shift ;;
  esac
done
# Anything left after `--` is also a tenant id.
while [[ $# -gt 0 ]]; do
  TENANTS+=("$1")
  shift
done

require_cmd git gcloud python3 curl

[[ -n "$PROJECT_ID" ]] || { log_error "--project-id is required."; exit 1; }
[[ -n "$STATE_BUCKET" ]] || { log_error "--state-bucket is required (forwarded to smoke_test.sh — see this script's header)."; exit 1; }

if [[ -n "$TENANTS_FILE" ]]; then
  if [[ "${#TENANTS[@]}" -gt 0 ]]; then
    log_error "Pass tenants either positionally or via --tenants-file, not both."
    exit 1
  fi
  [[ -f "$TENANTS_FILE" ]] || { log_error "--tenants-file not found: $TENANTS_FILE"; exit 1; }
  while IFS= read -r line; do
    line="${line%%#*}"                      # strip trailing comment
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [[ -n "$line" ]] || continue
    TENANTS+=("$line")
  done < "$TENANTS_FILE"
fi

if [[ "${#TENANTS[@]}" -eq 0 ]]; then
  log_error "No tenants given. Pass tenant ids positionally, or --tenants-file FILE (one per line)."
  exit 1
fi

for t in "${TENANTS[@]}"; do
  validate_tenant_id "$t"
done

if [[ -z "$CLOUDBUILD_SA" ]]; then
  CLOUDBUILD_SA="neurix-cloudbuild@${PROJECT_ID}.iam.gserviceaccount.com"
fi

if [[ -z "$BUILD_STAGING_BUCKET" ]]; then
  BUILD_STAGING_BUCKET="${PROJECT_ID}-build-staging"
fi

if [[ -z "$IMAGE_TAG" ]]; then
  IMAGE_TAG="$(cd "$REPO_ROOT" && git rev-parse --short HEAD)"
fi
# IMAGE_TAG is used both as the immutable image tag AND, unmodified, as the
# `release` Cloud Run label value below — GCP label values only accept
# lowercase letters, digits, underscore, and hyphen (<=63 chars). Refusing
# early on an invalid tag is better than a confusing gcloud error mid-release
# or a build that succeeds but can't be labeled.
if [[ ! "$IMAGE_TAG" =~ ^[a-z0-9_-]{1,63}$ ]]; then
  log_error "Invalid --image-tag '$IMAGE_TAG': must match [a-z0-9_-]{1,63} (it's also used as a Cloud Run label value)."
  exit 1
fi

log_info "Release plan: project=$PROJECT_ID region=$REGION image_tag=$IMAGE_TAG"
log_info "Tenant rollout order (first = canary): ${TENANTS[*]}"

# ---------------------------------------------------------------------------
# Migration safety check — runs before anything else, before a single image
# is built. See deploy/README.md's expand/contract rule.
# ---------------------------------------------------------------------------
log_info "=== Checking migrations for unapproved destructive statements ==="
python3 "$SCRIPT_DIR/check_migration_safety.py"

# ---------------------------------------------------------------------------
# Build + push the image ONCE for this whole release.
# ---------------------------------------------------------------------------
AR_REPO="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO_ID}"
IMAGE="${AR_REPO}/backend:${IMAGE_TAG}"

log_info "=== Building and pushing $IMAGE ==="
build_backend_image "$PROJECT_ID" "$REPO_ROOT/backend" "$IMAGE" "$CLOUDBUILD_SA" "$BUILD_STAGING_BUCKET"
log_info "Image built: $IMAGE"

# ---------------------------------------------------------------------------
# Per-tenant rollout.
# ---------------------------------------------------------------------------
CURRENT_TENANT=""
CURRENT_PREV_REVISION=""
REMAINING_TENANTS=()

on_exit() {
  local code=$?
  if [[ $code -ne 0 && -n "$CURRENT_TENANT" ]]; then
    log_error "=================================================================="
    log_error "Release STOPPED while rolling out tenant '$CURRENT_TENANT' (exit code $code)."
    if [[ -n "$CURRENT_PREV_REVISION" ]]; then
      log_error "Its previous revision was: $CURRENT_PREV_REVISION"
      log_error "Roll it back with:"
      log_error "  scripts/rollback.sh --project-id $PROJECT_ID --region $REGION $CURRENT_TENANT"
    else
      log_error "No previous revision was recorded for '$CURRENT_TENANT' (its Cloud Run update had not run yet, or this was its first deploy) — nothing to roll back for it."
    fi
    if [[ "${#REMAINING_TENANTS[@]}" -gt 0 ]]; then
      log_error "NOT touched (rollout stopped before reaching them): ${REMAINING_TENANTS[*]}"
    else
      log_error "No tenants remained after '$CURRENT_TENANT'."
    fi
    log_error "=================================================================="
  fi
}
trap on_exit EXIT

# tenant_platform_mode <tenant_id>
#
# Reads platform_mode straight out of the tenant's own .tfvars file (a plain
# text read — see lib.sh's read_tfvar_string — never a `terraform` call),
# purely so the same production opt-in gate every other script uses applies
# here too. Defaults to "remote_state" if the tenant has no .tfvars on this
# machine (e.g. release.sh run from a different checkout than provisioning
# was) or no platform_mode line — same default provision.sh itself uses.
tenant_platform_mode() {
  local tenant_id="$1"
  read_tfvar_string "$TF_DIR/tenants/${tenant_id}.tfvars" "platform_mode" "remote_state"
}

# ensure_sql_root_password
#
# Reads the platform's Cloud SQL admin (postgres) password the same,
# deterministic way for every tenant in this run (it's one password per
# project, not per tenant) — from the fixed "sql-root-password" secret
# scripts/provision.sh's normal (remote_state) flow creates. Falls back to
# an interactive prompt, exactly like provision.sh's migrate phase does for
# external platform mode, if that secret doesn't exist (e.g. this project's
# platform-level resources predate this package or were created by hand).
#
# Sets the global _SQL_ROOT_PASSWORD as a side effect (does NOT print it, so
# it must be called plainly, never as `x="$(ensure_sql_root_password)"` — a
# command substitution runs in a subshell, and a subshell's copy of
# _SQL_ROOT_PASSWORD is lost the instant the subshell exits, which would
# silently defeat the caching below and re-prompt interactively for every
# tenant). Fetches/prompts only once per run — every call after the first is
# a no-op.
_SQL_ROOT_PASSWORD=""
_SQL_ROOT_PASSWORD_FETCHED=0
ensure_sql_root_password() {
  if [[ "$_SQL_ROOT_PASSWORD_FETCHED" -eq 1 ]]; then
    return 0
  fi
  local pw
  if pw="$(gcloud secrets versions access latest --secret="$SQL_ROOT_SECRET" --project="$PROJECT_ID" 2>/dev/null)"; then
    log_info "Read Cloud SQL root password from Secret Manager secret '$SQL_ROOT_SECRET'."
    _SQL_ROOT_PASSWORD="$pw"
  else
    log_warn "Secret '$SQL_ROOT_SECRET' not found (or not readable) in project '$PROJECT_ID'."
    log_warn "Enter the Cloud SQL 'postgres' user password directly — it is never guessed or assumed."
    read_secret "Cloud SQL 'postgres' user password:" _SQL_ROOT_PASSWORD
  fi
  _SQL_ROOT_PASSWORD_FETCHED=1
}

rollout_tenant() {
  local tenant_id="$1"
  CURRENT_TENANT="$tenant_id"
  CURRENT_PREV_REVISION=""

  local platform_mode
  platform_mode="$(tenant_platform_mode "$tenant_id")"
  guard_tenant_production_opt_in "$PROJECT_ID" "$tenant_id" "$platform_mode"

  local db_name="neurix_${tenant_id}"                                 # must match terraform/tenant/main.tf's local.db_name
  local instance_connection_name="${PROJECT_ID}:${REGION}:${SQL_INSTANCE_NAME}"
  local service="neurix-backend-${tenant_id}"                         # must match terraform/tenant/cloud_run.tf's service name

  log_info "=================================================================="
  log_info "=== Tenant '$tenant_id' ==="
  log_info "=================================================================="

  # --- 1. Migrations -------------------------------------------------------
  guard_sql_target "$PROJECT_ID" "$tenant_id" "$instance_connection_name" "$db_name" "postgres"
  # Called plainly (not via command substitution) so its caching actually
  # works across tenants — see its own comment above.
  ensure_sql_root_password
  local db_password="$_SQL_ROOT_PASSWORD"
  log_info "Running backend/db/migrations against $db_name as postgres..."
  (
    cd "$REPO_ROOT/backend"
    export INSTANCE_CONNECTION_NAME="$instance_connection_name"
    export DB_USER="postgres"
    export DB_PASSWORD="$db_password"
    export DB_NAME="$db_name"
    python3 -m scripts.run_migrations
  )
  log_info "Migrations applied for '$tenant_id'."

  # --- 2. Cloud Run update ---------------------------------------------------
  CURRENT_PREV_REVISION="$(
    gcloud run services describe "$service" \
      --project="$PROJECT_ID" --region="$REGION" \
      --format='value(status.latestReadyRevisionName)' 2>/dev/null || true
  )"
  if [[ -n "$CURRENT_PREV_REVISION" ]]; then
    log_info "Previous revision for '$tenant_id': $CURRENT_PREV_REVISION (recorded — rollback target if anything below fails)"
  else
    log_warn "No previous ready revision found for '$service' (first deploy?) — nothing to roll back to if this fails."
  fi

  log_info "Updating Cloud Run service '$service' to $IMAGE (RELEASE=$IMAGE_TAG)..."
  gcloud run services update "$service" \
    --project="$PROJECT_ID" --region="$REGION" \
    --image="$IMAGE" \
    --update-env-vars="RELEASE=${IMAGE_TAG}" \
    --update-labels="release=${IMAGE_TAG}"
  log_info "Cloud Run service '$service' updated."

  # --- 3. Smoke test ---------------------------------------------------------
  log_info "Running smoke_test.sh for '$tenant_id'..."
  "$SCRIPT_DIR/smoke_test.sh" \
    --project-id "$PROJECT_ID" --tenant-id "$tenant_id" \
    --state-bucket "$STATE_BUCKET" --region "$REGION"

  log_info "Tenant '$tenant_id' released successfully (revision now serving RELEASE=$IMAGE_TAG)."
  CURRENT_TENANT=""
  CURRENT_PREV_REVISION=""
}

for ((i = 0; i < ${#TENANTS[@]}; i++)); do
  # Built with a loop, not "${TENANTS[@]:$((i + 1))}": under `set -u`, Bash
  # < 4.4 (macOS 3.2) treats an empty array expansion as an unbound variable.
  REMAINING_TENANTS=()
  for ((j = i + 1; j < ${#TENANTS[@]}; j++)); do
    REMAINING_TENANTS+=("${TENANTS[$j]}")
  done
  rollout_tenant "${TENANTS[$i]}"
done

log_info "=================================================================="
log_info "Release complete: ${TENANTS[*]} now serving $IMAGE (RELEASE=$IMAGE_TAG)."
log_info "=================================================================="
