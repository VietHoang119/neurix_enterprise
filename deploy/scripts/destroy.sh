#!/usr/bin/env bash
# Tears down what provision.sh created. Run this after testing — Cloud SQL
# runs 24/7 and bills whether used or not (~$36/month for db-g1-small in
# asia-southeast1 as of this writing; check current pricing yourself before
# relying on that number).
#
# Usage:
#   destroy.sh tenant   --project-id ID --tenant-id TID --state-bucket BUCKET [--region REGION] [--tfvars FILE]
#   destroy.sh platform --project-id ID --state-bucket BUCKET [--region REGION]
#   destroy.sh all      --project-id ID --tenant-id TID --state-bucket BUCKET [--region REGION] [--tfvars FILE]
#
# 'tenant' removes one tenant's resources (database, secrets, service
# account, storage, Cloud Run, Hosting site) but leaves the shared platform
# stack (and any other tenants) intact.
#
# 'platform' removes the shared platform stack, including the Cloud SQL
# instance — only do this once every tenant sharing it has already been
# torn down.
#
# 'all' does tenant then platform, for the common "I was just testing this
# tenant on a throwaway project" case.
#
# platform_mode is read from the tenant's own .tfvars (see provision.sh).
# In external platform mode (see deploy/README.md "Testing a tenant on the
# existing production platform"), 'platform' and 'all' are unavailable for
# the production project (guard_not_production always refuses it — use
# 'tenant' alone). 'tenant' alone goes through the layered production
# opt-in gate when --project-id is the production project, prints every
# resource in the tenant's state and requires typing the tenant id back,
# and refuses if the state isn't cleanly scoped to that tenant — it never
# touches the shared platform stack (Cloud SQL instance, Artifact Registry,
# shared bucket), which isn't in this state and was never managed by this
# package in external mode.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$DEPLOY_DIR/terraform"

# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

TARGET="${1:-}"
if [[ $# -gt 0 ]]; then
  shift
fi

PROJECT_ID=""
TENANT_ID=""
REGION="asia-southeast1"
STATE_BUCKET=""
TFVARS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --state-bucket) STATE_BUCKET="$2"; shift 2 ;;
    --tfvars) TFVARS="$2"; shift 2 ;;
    *) log_error "Unknown argument: $1"; exit 1 ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  log_error "Usage: destroy.sh <tenant|platform|all> --project-id ID [--tenant-id TID] --state-bucket BUCKET [--region REGION] [--tfvars FILE]"
  exit 1
fi

require_cmd terraform

[[ -n "$PROJECT_ID" ]] || { log_error "--project-id is required."; exit 1; }
[[ -n "$STATE_BUCKET" ]] || { log_error "--state-bucket is required."; exit 1; }

if [[ -n "$TENANT_ID" ]]; then
  validate_tenant_id "$TENANT_ID"
  TFVARS="${TFVARS:-$TF_DIR/tenants/${TENANT_ID}.tfvars}"
fi

# platform_mode lives in the tenant's own .tfvars — see provision.sh's own
# comment on this for why it isn't a CLI flag.
PLATFORM_MODE="remote_state"
if [[ -n "$TENANT_ID" ]]; then
  PLATFORM_MODE="$(read_tfvar_string "$TFVARS" "platform_mode" "remote_state")"
fi

# Same layered rule as provision.sh (see deploy/README.md "Hard safety rule"
# and "Testing a tenant on the existing production platform"): 'platform'
# and 'all' (which includes platform) always hard-refuse production, no
# override. 'tenant' alone goes through the opt-in gate.
case "$TARGET" in
  platform | all)
    guard_not_production "$PROJECT_ID"
    if [[ "$TARGET" == "all" && "$PROJECT_ID" == "$NEURIX_PRODUCTION_PROJECT_ID" ]]; then
      log_error "'all' always includes destroying the platform stack and is never available for the production project. Use 'tenant' alone to tear down just this tenant."
      exit 1
    fi
    ;;
  tenant)
    guard_tenant_production_opt_in "$PROJECT_ID" "$TENANT_ID" "$PLATFORM_MODE"
    ;;
esac

destroy_tenant() {
  [[ -n "$TENANT_ID" ]] || { log_error "--tenant-id is required to destroy a tenant."; exit 1; }
  [[ -f "$TFVARS" ]] || { log_error "tfvars file not found: $TFVARS"; exit 1; }

  log_warn "About to DESTROY every resource for tenant '$TENANT_ID' in project '$PROJECT_ID':"
  log_warn "  database, DB user, secrets, service account, storage grant/bucket, Cloud Run, Hosting site."
  log_warn "  THIS INCLUDES THE TENANT'S DATABASE (neurix_${TENANT_ID}) AND ALL DATA IN IT — this is"
  log_warn "  permanent; there is no undo once 'terraform destroy' below has run."
  confirm "Type 'yes' to proceed" || { log_info "Aborted."; exit 1; }

  terraform -chdir="$TF_DIR/tenant" init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(tenant_state_prefix "$PROJECT_ID" "$TENANT_ID")"

  if [[ "$PLATFORM_MODE" == "external" ]]; then
    require_tenant_scoped_state "$TENANT_ID"
    log_warn "External platform mode: destroy is scoped to ONLY this tenant's own Terraform state (prefix $(tenant_state_prefix "$PROJECT_ID" "$TENANT_ID")). The shared production platform (Cloud SQL instance, Artifact Registry, shared bucket) is never touched — it isn't in this state and was never managed by this package."
    read -r -p "Type the tenant id ('$TENANT_ID') to confirm you want to destroy it in project '$PROJECT_ID': " typed_tenant
    if [[ "$typed_tenant" != "$TENANT_ID" ]]; then
      log_error "Typed value did not match tenant_id. Aborting."
      exit 1
    fi
  fi

  terraform -chdir="$TF_DIR/tenant" destroy -input=false \
    -var-file="$TFVARS" \
    -var="project_id=$PROJECT_ID" \
    -var="region=$REGION" \
    -var="state_bucket=$STATE_BUCKET" \
    -var="tenant_id=$TENANT_ID" \
    -var="enable_app=true" \
    -var="backend_image=placeholder" \
    -var="frontend_url=https://${TENANT_ID}-neurix.web.app"

  log_info "Tenant '$TENANT_ID' destroyed."
  log_warn "Secret Manager secrets were deleted by Terraform, but this does NOT remove:"
  log_warn "  - the Firebase Web App registration (delete manually in the Firebase console if desired)"
  log_warn "  - any images already pushed to Artifact Registry for this tenant (harmless, minor storage cost)"
}

# require_tenant_scoped_state <tenant_id>
#
# External-mode-only safety net (see deploy/README.md "Testing a tenant on
# the existing production platform"): prints every resource address
# currently in this tenant's own Terraform state, then refuses to proceed
# unless (a) every resource address is one terraform/tenant can actually
# produce, and (b) the state's own `tenant_id` output matches the tenant_id
# this destroy was invoked with (see check_tenant_scoped_state.py). Either
# check failing means the wrong --tenant-id/--tfvars/--state-bucket
# combination pointed this destroy at a state that isn't cleanly this
# tenant's own.
require_tenant_scoped_state() {
  local tenant_id="$1"
  local resource_count
  resource_count="$(terraform -chdir="$TF_DIR/tenant" state list | wc -l | tr -d ' ')"

  if [[ "$resource_count" -eq 0 ]]; then
    log_warn "Tenant state is empty — nothing to destroy (already torn down, or never applied)."
    return 0
  fi

  log_info "Resources currently in tenant '$tenant_id's Terraform state:"
  terraform -chdir="$TF_DIR/tenant" state list >&2

  local state_json
  state_json="$(terraform -chdir="$TF_DIR/tenant" show -json)"
  if ! printf '%s' "$state_json" | python3 "$SCRIPT_DIR/check_tenant_scoped_state.py" "$tenant_id"; then
    log_error "Refusing to destroy: this tenant Terraform state is not cleanly scoped to tenant_id '$tenant_id' (see check_tenant_scoped_state.py's output above)."
    log_error "This could mean the wrong --tenant-id, --tfvars, or --state-bucket was passed. Aborting without making changes."
    exit 1
  fi
}

destroy_platform() {
  log_warn "About to DESTROY the shared platform stack in project '$PROJECT_ID':"
  log_warn "  Cloud SQL instance (ALL tenant databases), Artifact Registry repo, shared bucket, Cloud Build SA, Firebase project link."
  log_warn "Only do this once every tenant sharing this platform has already been destroyed."
  confirm "Type 'yes' to proceed" || { log_info "Aborted."; exit 1; }

  terraform -chdir="$TF_DIR/platform" init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(platform_state_prefix "$PROJECT_ID")"

  # deletion_protection defaults to true (see terraform/platform/variables.tf)
  # so that a stray `terraform apply` can never silently drop the database.
  # Destroying on purpose means explicitly turning it off first, in its own
  # apply — Terraform refuses to destroy a google_sql_database_instance
  # while deletion_protection=true is still the value in state.
  log_info "Disabling Cloud SQL deletion_protection before destroy..."
  terraform -chdir="$TF_DIR/platform" apply -input=false -auto-approve \
    -var="project_id=$PROJECT_ID" \
    -var="region=$REGION" \
    -var="sql_deletion_protection=false"

  terraform -chdir="$TF_DIR/platform" destroy -input=false \
    -var="project_id=$PROJECT_ID" \
    -var="region=$REGION" \
    -var="sql_deletion_protection=false"

  log_info "Platform stack destroyed."
}

case "$TARGET" in
  tenant) destroy_tenant ;;
  platform) destroy_platform ;;
  all) destroy_tenant; destroy_platform ;;
  *)
    log_error "Unknown target: $TARGET (expected tenant, platform, or all)"
    exit 1
    ;;
esac
