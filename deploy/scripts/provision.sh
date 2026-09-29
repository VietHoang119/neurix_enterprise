#!/usr/bin/env bash
# Orchestrates the 5-phase tenant provisioning flow described in
# deploy/README.md. Each phase is callable on its own (idempotent — running
# a phase twice must not break anything) or via `all`, which runs every
# phase in order and pauses interactively at the Slack step.
#
# Usage:
#   provision.sh <phase> --project-id ID --tenant-id TID --state-bucket BUCKET \
#     [--region REGION] [--tfvars FILE] [--image IMAGE]
#
# Phases (in order):
#   platform          Terraform phase 1: platform stack (once per project).
#   tenant-resources  Terraform phase 1: tenant resources except Cloud Run/Hosting.
#   migrate           Run migrations + grants; build and push the backend image.
#   slack-secrets     Manual pause: prompts for the Slack bot token + signing
#                     secret (and Gemini API key) and stores them in Secret
#                     Manager. Requires a human to have created+installed the
#                     Slack app first (this phase prints how).
#   app               Terraform phase 2: Cloud Run + Firebase Hosting site.
#   frontend          Build + deploy the frontend; print the Cloud Run URL
#                     and the three Slack URLs to paste into the Slack app.
#   all               Run every phase above in order.
#
# platform_mode ("remote_state" default, or "external") is read from the
# tenant's own .tfvars file, not a CLI flag — see deploy/README.md "Testing
# a tenant on the existing production platform". In external mode:
#   - 'platform' and 'all' are unavailable for the production project
#     (guard_not_production always refuses it; run the other phases
#     individually instead).
#   - Every other phase goes through the layered production opt-in gate
#     (NEURIX_ALLOW_PRODUCTION_PROJECT=yes + a typed tenant_id confirmation)
#     when --project-id is the production project.
#   - terraform/tenant applies are plan-then-verify-create-only before
#     apply (see check_create_only_plan.py).
#
# See deploy/README.md for prerequisites and the full walkthrough.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/.." && pwd)"
TF_DIR="$DEPLOY_DIR/terraform"
STATE_DIR="$SCRIPT_DIR/.provision-state"

# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PROJECT_ID=""
TENANT_ID=""
REGION="asia-southeast1"
STATE_BUCKET=""
TFVARS=""
IMAGE_OVERRIDE=""
PHASE="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --state-bucket) STATE_BUCKET="$2"; shift 2 ;;
    --tfvars) TFVARS="$2"; shift 2 ;;
    --image) IMAGE_OVERRIDE="$2"; shift 2 ;;
    *) log_error "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "$PHASE" ]]; then
  log_error "Usage: provision.sh <phase> --project-id ID --tenant-id TID --state-bucket BUCKET [--region REGION] [--tfvars FILE] [--image IMAGE]"
  log_error "Phases: platform tenant-resources migrate slack-secrets app frontend all"
  exit 1
fi

require_cmd terraform gcloud python3

[[ -n "$PROJECT_ID" ]] || { log_error "--project-id is required."; exit 1; }
[[ -n "$STATE_BUCKET" ]] || { log_error "--state-bucket is required."; exit 1; }

mkdir -p "$STATE_DIR"
IMAGE_STATE_FILE="$STATE_DIR/${PROJECT_ID}-${TENANT_ID}.image"

if [[ -n "$TENANT_ID" ]]; then
  validate_tenant_id "$TENANT_ID"
  TFVARS="${TFVARS:-$TF_DIR/tenants/${TENANT_ID}.tfvars}"
fi

# platform_mode lives in the tenant's own .tfvars (alongside dedicated_bucket,
# gemini_model, etc.) rather than behind a CLI flag — it's a fixed property
# of a given tenant/environment, not something to retype on every command.
# Defaults to "remote_state" (the normal flow) when unset or when there is
# no tenant in play for this phase (e.g. 'platform').
PLATFORM_MODE="remote_state"
if [[ -n "$TENANT_ID" ]]; then
  PLATFORM_MODE="$(read_tfvar_string "$TFVARS" "platform_mode" "remote_state")"
fi

# ---------------------------------------------------------------------------
# Safety rule, layered (see deploy/README.md "Hard safety rule" and "Testing
# a tenant on the existing production platform"):
#
#   - 'platform' and 'all' (which always runs 'platform' first) ALWAYS
#     refuse the production project outright — guard_not_production has no
#     override of any kind, for any phase that touches platform/.
#   - Every other, tenant-only phase goes through the layered opt-in gate
#     (guard_tenant_production_opt_in), which is a no-op for any project
#     other than production, and for production requires external platform
#     mode + an explicit env var + a typed tenant_id confirmation.
#
# Checked before any other side effect, so a phase can never be invoked
# without the right guard having run first in the same process.
# ---------------------------------------------------------------------------
case "$PHASE" in
  platform | all)
    guard_not_production "$PROJECT_ID"
    if [[ "$PHASE" == "all" && "$PROJECT_ID" == "$NEURIX_PRODUCTION_PROJECT_ID" ]]; then
      # Unreachable in practice (guard_not_production above already exited),
      # kept as an explicit, readable statement of intent: 'all' is never
      # available for production, even in external mode — it always runs
      # 'platform' first, which is exactly what external mode exists to
      # avoid touching. Run the individual tenant-only phases instead.
      log_error "'all' always includes the platform phase and is never available for the production project. Run the tenant-only phases individually (tenant-resources, migrate, slack-secrets, app, frontend)."
      exit 1
    fi
    ;;
  *)
    guard_tenant_production_opt_in "$PROJECT_ID" "$TENANT_ID" "$PLATFORM_MODE"
    ;;
esac

tf_platform() {
  terraform -chdir="$TF_DIR/platform" "$@"
}

tf_tenant() {
  terraform -chdir="$TF_DIR/tenant" "$@"
}

init_platform() {
  tf_platform init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(platform_state_prefix "$PROJECT_ID")"
}

init_tenant() {
  tf_tenant init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(tenant_state_prefix "$PROJECT_ID" "$TENANT_ID")"
}

require_tenant_args() {
  [[ -n "$TENANT_ID" ]] || { log_error "--tenant-id is required for phase '$PHASE'."; exit 1; }
  if [[ ! -f "$TFVARS" ]]; then
    log_error "tfvars file not found: $TFVARS"
    log_error "Copy $TF_DIR/tenants/example.tfvars to $TFVARS and fill it in first."
    exit 1
  fi
}

CHECK_PLAN_SCRIPT="$SCRIPT_DIR/check_create_only_plan.py"

# tf_platform_apply <var-args...>
#
# Applies terraform/platform via plan -> verify (check_create_only_plan.py
# --mode platform-reapply) -> apply exactly that saved plan. On a brand-new
# project this plan is all "create" and passes trivially; the check earns
# its keep on every later re-apply (a new sql_tier, a label change, a new
# API to enable), where it still allows in-place updates but refuses to let
# the run proceed if the plan would delete or replace the Cloud SQL
# instance or any bucket — whether from an intentional-looking config edit
# or an unintentional one (e.g. a force-new attribute change nobody meant to
# make). See deploy/README.md "Releasing code vs changing infrastructure".
tf_platform_apply() {
  local planfile="$STATE_DIR/${PROJECT_ID}-platform.tfplan"
  log_info "Planning platform apply, then verifying it never deletes/replaces a stateful resource..."
  tf_platform plan -input=false -out="$planfile" "$@"
  if ! tf_platform show -json "$planfile" | python3 "$CHECK_PLAN_SCRIPT" --mode platform-reapply; then
    rm -f "$planfile"
    log_error "Refusing to apply: the plan above would delete or replace a stateful platform resource (Cloud SQL instance, database, or bucket). See check_create_only_plan.py's output above."
    exit 1
  fi
  log_info "Plan verified safe. Applying..."
  tf_platform apply -input=false "$planfile"
  rm -f "$planfile"
}

# tf_tenant_apply <var-args...>
#
# Applies terraform/tenant with the given -var args. In the normal
# (remote_state) flow this is a plain `terraform apply`, exactly as before —
# updates (e.g. a new backend_image on redeploy) are expected and fine.
#
# In external platform mode, this instead does plan -> verify the plan is
# create-only (check_create_only_plan.py; see deploy/README.md "Terraform
# safety net") -> apply exactly that saved plan. This is the safety net that
# stops a first apply against a platform stack that already exists (e.g.
# production) from ever updating, replacing, or deleting something that's
# already there — only ever creating new, tenant-suffixed resources.
tf_tenant_apply() {
  if [[ "$PLATFORM_MODE" != "external" ]]; then
    tf_tenant apply -input=false "$@"
    return
  fi

  local planfile="$STATE_DIR/${PROJECT_ID}-${TENANT_ID}.tfplan"
  log_info "External platform mode: planning, then verifying the plan only creates new resources..."
  tf_tenant plan -input=false -out="$planfile" "$@"
  if ! tf_tenant show -json "$planfile" | python3 "$CHECK_PLAN_SCRIPT"; then
    rm -f "$planfile"
    log_error "Refusing to apply: the plan above would change or delete an existing resource. See check_create_only_plan.py's output above for exactly which one(s)."
    exit 1
  fi
  log_info "Plan verified create-only. Applying..."
  tf_tenant apply -input=false "$planfile"
  rm -f "$planfile"
}

# ---------------------------------------------------------------------------
# Phase 1a: platform (once per project)
# ---------------------------------------------------------------------------
phase_platform() {
  log_info "=== Terraform phase 1: platform stack ($PROJECT_ID) ==="
  init_platform
  tf_platform_apply \
    -var="project_id=$PROJECT_ID" \
    -var="region=$REGION"
  log_info "Platform stack applied."
}

# ---------------------------------------------------------------------------
# Phase 1b: tenant resources, enable_app=false (Terraform phase 1 for tenant)
# ---------------------------------------------------------------------------
phase_tenant_resources() {
  require_tenant_args
  log_info "=== Terraform phase 1: tenant resources ($TENANT_ID, platform_mode=$PLATFORM_MODE) ==="
  init_tenant
  tf_tenant_apply \
    -var-file="$TFVARS" \
    -var="project_id=$PROJECT_ID" \
    -var="region=$REGION" \
    -var="state_bucket=$STATE_BUCKET" \
    -var="tenant_id=$TENANT_ID" \
    -var="enable_app=false"
  log_info "Tenant resources applied (database, secrets, service account, storage)."
}

# ---------------------------------------------------------------------------
# Phase 2: migrate + grants + build/push image
# ---------------------------------------------------------------------------
phase_migrate() {
  require_tenant_args
  log_info "=== Phase 2: migrate database and build backend image ($TENANT_ID, platform_mode=$PLATFORM_MODE) ==="

  # Every value needed here (instance, db name/user, Artifact Registry repo,
  # Cloud Build SA) comes from terraform/tenant's own outputs, which already
  # resolve to the right thing in either platform_mode — see
  # terraform/tenant/platform_source.tf's local.platform. No `tf_platform`
  # call is needed (or possible, in external mode: there is no platform
  # Terraform state for production).
  init_tenant >/dev/null

  local instance_connection_name db_name db_app_user ar_repo cloudbuild_sa build_staging_bucket
  instance_connection_name="$(tf_tenant output -raw sql_instance_connection_name)"
  ar_repo="$(tf_tenant output -raw artifact_registry_repository)"
  cloudbuild_sa="$(tf_tenant output -raw cloudbuild_service_account_email)"
  build_staging_bucket="$(tf_tenant output -raw build_staging_bucket_name)"
  db_name="$(tf_tenant output -raw db_name)"
  db_app_user="$(tf_tenant output -raw db_user)"

  guard_sql_target "$PROJECT_ID" "$TENANT_ID" "$instance_connection_name" "$db_name" "postgres"

  local root_password
  if [[ "$PLATFORM_MODE" == "external" ]]; then
    log_warn "External platform mode: the Cloud SQL admin (postgres) password for the existing instance is not in Terraform state or a known Secret Manager secret."
    log_warn "It is never guessed or assumed here — enter it directly."
    read_secret "Cloud SQL 'postgres' user password for instance '$instance_connection_name':" root_password
  else
    local sql_root_secret
    sql_root_secret="$(tf_tenant output -raw sql_root_password_secret_id)"
    log_info "Reading Cloud SQL root password from Secret Manager (never printed)..."
    root_password="$(gcloud secrets versions access latest --secret="$sql_root_secret" --project="$PROJECT_ID")"
  fi

  log_info "Running backend/scripts/run_migrations.py against $db_name as postgres..."
  (
    cd "$REPO_ROOT/backend"
    export INSTANCE_CONNECTION_NAME="$instance_connection_name"
    export DB_USER="postgres"
    export DB_PASSWORD="$root_password"
    export DB_NAME="$db_name"
    python3 -m scripts.run_migrations
  )

  guard_sql_target "$PROJECT_ID" "$TENANT_ID" "$instance_connection_name" "$db_name" "postgres"
  log_info "Granting $db_app_user access (existing tables/sequences + ALTER DEFAULT PRIVILEGES for future migrations)..."
  INSTANCE_CONNECTION_NAME="$instance_connection_name" \
    DB_USER="postgres" \
    DB_PASSWORD="$root_password" \
    DB_NAME="$db_name" \
    python3 "$SCRIPT_DIR/grant_tenant.py" \
      --db-app-user "$db_app_user" \
      --migration-role postgres \
      --project-id "$PROJECT_ID" \
      --tenant-id "$TENANT_ID"
  unset root_password

  local image_tag image
  image_tag="${TENANT_ID}-$(date -u +%Y%m%d-%H%M%S)"
  image="${ar_repo}/backend:${image_tag}"

  log_info "Building and pushing $image via Cloud Build..."
  build_backend_image "$PROJECT_ID" "$REPO_ROOT/backend" "$image" "$cloudbuild_sa" "$build_staging_bucket"

  echo "$image" > "$IMAGE_STATE_FILE"
  log_info "Image built: $image"
  log_info "(recorded in $IMAGE_STATE_FILE — the 'app' phase reads it automatically, or pass --image explicitly)"
}

# ---------------------------------------------------------------------------
# Phase 3: manual pause — Slack app creation + secret handoff
# ---------------------------------------------------------------------------
phase_slack_secrets() {
  require_tenant_args
  log_info "=== Phase 3: Slack app (manual) + secret handoff ($TENANT_ID) ==="

  cat >&2 <<EOF

------------------------------------------------------------------
MANUAL STEP — do this in the Slack UI now:

  1. Go to https://api.slack.com/apps -> "Create New App" -> "From an app manifest".
  2. Pick the workspace for tenant '$TENANT_ID'.
  3. Paste the contents of deploy/slack/slack-manifest.yml as the manifest
     template (adjust the display name for this tenant if you like — the
     manifest's URLs use the fake host
     'replace-with-cloud-run-url.example.com' as a placeholder; you'll
     replace it with the real Cloud Run host after phase 5, once the
     backend is deployed).
  4. Create the app, then install it to the workspace.
  5. Go to "OAuth & Permissions" and copy the Bot User OAuth Token
     (starts with xoxb-).
  6. Go to "Basic Information" -> "App Credentials" and copy the
     Signing Secret.
  7. (Optional now, can be done later) Have your Gemini/Google AI API key
     ready if you want to set it in the same pass.

Come back here once you have the bot token and signing secret.
------------------------------------------------------------------

EOF

  if ! confirm "Ready to enter the Slack bot token and signing secret?"; then
    log_info "Aborted — re-run 'slack-secrets' when ready."
    exit 1
  fi

  local bot_token signing_secret gemini_key
  read_secret "Slack bot token (xoxb-...):" bot_token
  read_secret "Slack signing secret:" signing_secret

  log_info "Storing secrets in Secret Manager (values are never echoed)..."
  gcloud_secret_add_version "$PROJECT_ID" "slack-bot-token-${TENANT_ID}" "$bot_token"
  gcloud_secret_add_version "$PROJECT_ID" "slack-signing-secret-${TENANT_ID}" "$signing_secret"
  unset bot_token signing_secret

  if [[ "$PLATFORM_MODE" == "external" ]]; then
    log_info "External platform mode: this tenant's runtime service account was already granted read access (by the tenant-resources phase) to the EXISTING Gemini secret named in external_gemini_api_key_secret_id — no new secret or version to set here."
  elif confirm "Set the Gemini API key now too? (You can skip and add it later with: gcloud secrets versions add gemini-api-key-${TENANT_ID})"; then
    read_secret "Gemini API key:" gemini_key
    gcloud_secret_add_version "$PROJECT_ID" "gemini-api-key-${TENANT_ID}" "$gemini_key"
    unset gemini_key
  else
    log_warn "Gemini API key not set. Cloud Run will fail to start GEMINI calls (GOOGLE_API_KEY empty) until you add a version to gemini-api-key-${TENANT_ID}."
  fi

  log_info "Secrets stored for tenant '$TENANT_ID'."
}

# ---------------------------------------------------------------------------
# Phase 4: Terraform phase 2 — Cloud Run + Firebase Hosting site
# ---------------------------------------------------------------------------
phase_app() {
  require_tenant_args
  log_info "=== Terraform phase 2: Cloud Run + Firebase Hosting ($TENANT_ID, platform_mode=$PLATFORM_MODE) ==="

  local image
  if [[ -n "$IMAGE_OVERRIDE" ]]; then
    image="$IMAGE_OVERRIDE"
  elif [[ -f "$IMAGE_STATE_FILE" ]]; then
    image="$(cat "$IMAGE_STATE_FILE")"
  else
    log_error "No backend image known. Run the 'migrate' phase first, or pass --image explicitly."
    exit 1
  fi

  # Firebase Hosting site URLs are deterministic (https://<site-id>.web.app),
  # so FRONTEND_URL for Cloud Run's CORS can be computed up front — it does
  # not need the Hosting site to exist yet, breaking what would otherwise be
  # a circular dependency between Cloud Run and Hosting.
  local frontend_url="https://${TENANT_ID}-neurix.web.app"

  init_tenant >/dev/null
  tf_tenant_apply \
    -var-file="$TFVARS" \
    -var="project_id=$PROJECT_ID" \
    -var="region=$REGION" \
    -var="state_bucket=$STATE_BUCKET" \
    -var="tenant_id=$TENANT_ID" \
    -var="enable_app=true" \
    -var="backend_image=$image" \
    -var="frontend_url=$frontend_url"

  log_info "Cloud Run + Hosting site created."
  log_info "Cloud Run URL: $(tf_tenant output -raw cloud_run_url)"

  cat >&2 <<'EOF'

------------------------------------------------------------------
MANUAL — known unknown, not automated (see deploy/README.md):

  Firebase Auth's Google sign-in provider cannot be reliably enabled via
  Terraform/CLI — it needs a click in the Firebase Console:
    Firebase Console -> Authentication -> Sign-in method -> Add provider
    -> Google -> Enable -> set a support email -> Save.
  Do this now if you haven't already for this project.
------------------------------------------------------------------

EOF
}

# ---------------------------------------------------------------------------
# Phase 5: frontend build + deploy, print Cloud Run + Slack URLs
#
# Everything this phase builds and deploys lives in a scratch workspace
# created by `mktemp -d`, outside frontend/ and outside the repo entirely,
# removed unconditionally by the trap below. This phase never writes an env
# file into frontend/, never builds into frontend/dist, and never touches
# frontend/.firebaserc or frontend/firebase.json — see deploy/README.md
# "Frontend build/deploy isolation" for why: a later *production* frontend
# build/deploy run from this same working tree must never pick up a test
# tenant's backend URL, Firebase config, or hosting target by accident.
# ---------------------------------------------------------------------------
phase_frontend() {
  require_tenant_args
  require_cmd npm python3
  log_info "=== Phase 5: frontend build + deploy ($TENANT_ID) ==="

  init_tenant >/dev/null
  local cloud_run_url site_id firebase_project_id
  cloud_run_url="$(tf_tenant output -raw cloud_run_url)"
  site_id="$(tf_tenant output -raw hosting_site_id)"
  # tenant/'s own firebase_project_id output resolves correctly in either
  # platform_mode (see terraform/tenant/platform_source.tf) — no
  # `tf_platform` call needed (and none is possible in external mode).
  firebase_project_id="$(tf_tenant output -raw firebase_project_id 2>/dev/null || echo "$PROJECT_ID")"

  if [[ -z "$cloud_run_url" || "$cloud_run_url" == "null" ]]; then
    log_error "Cloud Run URL not available — run the 'app' phase first."
    exit 1
  fi
  if [[ -z "$site_id" || "$site_id" == "null" ]]; then
    log_error "Hosting site id not available — run the 'app' phase first."
    exit 1
  fi

  log_info "Ensuring a Firebase Web App exists for this project..."
  local app_id
  app_id="$(gcloud firebase apps list --project="$PROJECT_ID" --filter="displayName:'Neurix ${TENANT_ID}'" --format='value(appId)' 2>/dev/null | head -n1 || true)"
  if [[ -z "$app_id" ]]; then
    log_info "Creating Firebase Web App 'Neurix ${TENANT_ID}'..."
    app_id="$(gcloud firebase apps create web "Neurix ${TENANT_ID}" --project="$PROJECT_ID" --format='value(appId)')"
  fi

  log_info "Fetching Firebase Web SDK config..."
  local sdkconfig_json
  sdkconfig_json="$(gcloud firebase apps sdkconfig web "$app_id" --project="$PROJECT_ID" --format=json)"

  # Deliberately NOT `local`: the EXIT trap below fires when the whole
  # script process exits, which happens after this function has already
  # returned (including the case where `set -e` unwinds straight past this
  # function on a later command's failure) — a `local` here would already
  # be out of scope by then, making `$workdir` an unbound variable under
  # `set -u` at the moment the trap actually runs.
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/neurix-frontend-${TENANT_ID}.XXXXXX")"
  # The EXIT trap alone covers success and any `exit` triggered by `set -e`
  # partway through this phase. SIGINT/SIGTERM need their own traps that
  # explicitly `exit` afterward — a bare `trap ... INT TERM` with no `exit`
  # inside the handler only runs the handler and then resumes the script
  # where it left off (bash's documented behavior for a caught signal with
  # no exit in its trap), which would let a Ctrl-C during the build/deploy
  # step limp forward into later commands against an already-removed
  # workdir instead of actually aborting.
  trap 'rm -rf "$workdir"' EXIT
  trap 'rm -rf "$workdir"; exit 130' INT
  trap 'rm -rf "$workdir"; exit 143' TERM

  local dist_dir="$workdir/dist"
  local tmp_config="$workdir/firebase.tenant-deploy.json"

  if [[ "$site_id" == "$firebase_project_id" ]]; then
    log_error "Refusing: hosting_site_id ('$site_id') equals the project's default site ('$firebase_project_id')."
    log_error "This guard exists to stop a tenant deploy from ever landing on the project's default Hosting site."
    exit 1
  fi

  log_info "Writing a tenant-scoped, throwaway Firebase Hosting config to $tmp_config (frontend/.firebaserc and frontend/firebase.json are never read or written)..."
  python3 - "$tmp_config" "$site_id" "$dist_dir" <<'PYEOF'
import json
import sys

config_path, site_id, dist_dir = sys.argv[1:4]
config = {
    "hosting": {
        "site": site_id,
        "public": dist_dir,
        "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
        "rewrites": [{"source": "**", "destination": "/index.html"}],
    }
}
with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
PYEOF
  guard_hosting_config_site "$tmp_config" "$site_id" "$firebase_project_id"

  log_info "Building frontend into $dist_dir (VITE_* values are passed as env vars to the build process only, never written to frontend/.env — Vite gives a prefixed process env var priority over any .env file)..."
  local build_env_script
  build_env_script="$(python3 - "$sdkconfig_json" "$cloud_run_url" <<'PYEOF'
import json
import shlex
import sys

cfg = json.loads(sys.argv[1])
cloud_run_url = sys.argv[2]
pairs = {
    "VITE_FIREBASE_API_KEY": cfg["apiKey"],
    "VITE_FIREBASE_AUTH_DOMAIN": cfg["authDomain"],
    "VITE_FIREBASE_PROJECT_ID": cfg["projectId"],
    "VITE_FIREBASE_APP_ID": cfg["appId"],
    "VITE_API_BASE_URL": cloud_run_url,
}
for key, value in pairs.items():
    print(f"export {key}={shlex.quote(value)}")
PYEOF
  )"
  (
    eval "$build_env_script"
    cd "$REPO_ROOT/frontend"
    npm install --no-audit --no-fund
    npm run build -- --outDir "$dist_dir" --emptyOutDir
  )

  log_info "Deploying to Firebase Hosting site '$site_id' via the throwaway --config above..."
  local -a deploy_cmd=(npx --yes firebase-tools deploy --only hosting --config "$tmp_config" --project="$PROJECT_ID")
  guard_hosting_deploy_args "$tmp_config" "${deploy_cmd[@]}"
  log_info "Deploy command: ${deploy_cmd[*]}"

  if ! (cd "$workdir" && "${deploy_cmd[@]}"); then
    log_error "Hosting deploy failed. Nothing under frontend/ was touched by this phase; fix the underlying issue and re-run 'frontend'."
    exit 1
  fi

  cat >&2 <<EOF

==================================================================
Tenant '$TENANT_ID' is deployed.

Frontend:   https://${site_id}.web.app
Backend:    $cloud_run_url

Paste these into the Slack app's configuration (api.slack.com/apps):
  Slash Commands      /neurix      -> ${cloud_run_url}/slack/commands
  Interactivity URL                -> ${cloud_run_url}/slack/interactions
  Event Subscriptions Request URL  -> ${cloud_run_url}/slack/events

Reinstall the Slack app to the workspace after saving these URLs.

Remaining manual step (see deploy/README.md "Known unknowns"):
  Enable the Google sign-in provider in Firebase Auth for project
  '$firebase_project_id' if you haven't already (Firebase Console ->
  Authentication -> Sign-in method).
==================================================================
EOF
}

phase_all() {
  phase_platform
  phase_tenant_resources
  phase_migrate
  phase_slack_secrets
  phase_app
  phase_frontend
}

case "$PHASE" in
  platform) phase_platform ;;
  tenant-resources) phase_tenant_resources ;;
  migrate) phase_migrate ;;
  slack-secrets) phase_slack_secrets ;;
  app) phase_app ;;
  frontend) phase_frontend ;;
  all) phase_all ;;
  *)
    log_error "Unknown phase: $PHASE"
    log_error "Phases: platform tenant-resources migrate slack-secrets app frontend all"
    exit 1
    ;;
esac
