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

guard_not_production "$PROJECT_ID"
require_cmd terraform

[[ -n "$PROJECT_ID" ]] || { log_error "--project-id is required."; exit 1; }
[[ -n "$STATE_BUCKET" ]] || { log_error "--state-bucket is required."; exit 1; }

if [[ -n "$TENANT_ID" ]]; then
  validate_tenant_id "$TENANT_ID"
  TFVARS="${TFVARS:-$TF_DIR/tenants/${TENANT_ID}.tfvars}"
fi

destroy_tenant() {
  [[ -n "$TENANT_ID" ]] || { log_error "--tenant-id is required to destroy a tenant."; exit 1; }
  [[ -f "$TFVARS" ]] || { log_error "tfvars file not found: $TFVARS"; exit 1; }

  log_warn "About to DESTROY every resource for tenant '$TENANT_ID' in project '$PROJECT_ID':"
  log_warn "  database, DB user, secrets, service account, storage grant/bucket, Cloud Run, Hosting site."
  confirm "Type 'yes' to proceed" || { log_info "Aborted."; exit 1; }

  terraform -chdir="$TF_DIR/tenant" init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(tenant_state_prefix "$PROJECT_ID" "$TENANT_ID")"

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
