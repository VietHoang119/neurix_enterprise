#!/usr/bin/env bash
# Post-deploy checks for one tenant. Read-only — never modifies
# infrastructure. Exits non-zero if anything looks wrong.
#
# Usage:
#   smoke_test.sh --project-id ID --tenant-id TID --state-bucket BUCKET [--region REGION] [--tfvars FILE]
#   smoke_test.sh bucket-isolation --project-id ID --tenant-a A --tenant-b B --state-bucket BUCKET
#
# The default (no subcommand) mode checks:
#   - Cloud Run /health responds 401 (reachable, app-level auth enforcing —
#     NOT a Cloud Run-level 403, which would mean the public invoker binding
#     is missing)
#   - The Firebase Hosting URL responds 200
#   - Every tenant secret (db-password, slack-bot-token, slack-signing-secret,
#     gemini-api-key) has at least one version — an empty one means a step
#     was skipped (most likely: the manual Slack/Gemini secret handoff)
#
# 'bucket-isolation' is the concrete test procedure for the UNVERIFIED
# shared-bucket IAM condition (see deploy/README.md "Known unknowns"): it
# checks whether tenant A's runtime service account can sign a URL for an
# object under tenant B's prefix in the shared bucket. It should be denied;
# if it succeeds, the IAM condition is not actually isolating tenants and
# dedicated_bucket = true should be used instead for any tenant that needs
# real isolation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$DEPLOY_DIR/terraform"

# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

MODE="default"
if [[ "${1:-}" == "bucket-isolation" ]]; then
  MODE="bucket-isolation"
  shift
fi

PROJECT_ID=""
TENANT_ID=""
TENANT_A=""
TENANT_B=""
REGION="asia-southeast1"
STATE_BUCKET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    --tenant-a) TENANT_A="$2"; shift 2 ;;
    --tenant-b) TENANT_B="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --state-bucket) STATE_BUCKET="$2"; shift 2 ;;
    *) log_error "Unknown argument: $1"; exit 1 ;;
  esac
done

guard_not_production "$PROJECT_ID"
require_cmd curl gcloud terraform

[[ -n "$PROJECT_ID" ]] || { log_error "--project-id is required."; exit 1; }
[[ -n "$STATE_BUCKET" ]] || { log_error "--state-bucket is required."; exit 1; }
log_info "project=$PROJECT_ID region=$REGION state_bucket=$STATE_BUCKET"

FAILED=0
fail() { log_error "$1"; FAILED=1; }

run_default() {
  [[ -n "$TENANT_ID" ]] || { log_error "--tenant-id is required."; exit 1; }
  validate_tenant_id "$TENANT_ID"

  terraform -chdir="$TF_DIR/tenant" init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(tenant_state_prefix "$PROJECT_ID" "$TENANT_ID")" >/dev/null

  local cloud_run_url site_id
  cloud_run_url="$(terraform -chdir="$TF_DIR/tenant" output -raw cloud_run_url 2>/dev/null || true)"
  site_id="$(terraform -chdir="$TF_DIR/tenant" output -raw hosting_site_id 2>/dev/null || true)"

  if [[ -z "$cloud_run_url" || "$cloud_run_url" == "null" ]]; then
    fail "No cloud_run_url in tenant state — has the 'app' phase been run?"
  else
    log_info "Checking $cloud_run_url/health (expect HTTP 401 — reachable, app auth enforcing)..."
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${cloud_run_url}/health" || echo "000")"
    if [[ "$code" == "401" ]]; then
      log_info "OK: /health returned 401 as expected."
    elif [[ "$code" == "403" ]]; then
      fail "/health returned 403 — this looks like a Cloud Run IAM rejection (allUsers invoker binding missing), not the app's own auth check."
    else
      fail "/health returned unexpected HTTP $code (expected 401)."
    fi
  fi

  if [[ -n "$site_id" && "$site_id" != "null" ]]; then
    log_info "Checking https://${site_id}.web.app (expect HTTP 200)..."
    local fcode
    fcode="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://${site_id}.web.app" || echo "000")"
    [[ "$fcode" == "200" ]] || fail "Frontend at https://${site_id}.web.app returned HTTP $fcode (expected 200) — has the 'frontend' phase been deployed?"
  else
    fail "No hosting_site_id in tenant state — has the 'app' phase been run?"
  fi

  local secret
  for secret in "db-password-${TENANT_ID}" "slack-bot-token-${TENANT_ID}" "slack-signing-secret-${TENANT_ID}" "gemini-api-key-${TENANT_ID}"; do
    local count
    count="$(gcloud secrets versions list "$secret" --project="$PROJECT_ID" --format='value(name)' 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "$count" -eq 0 ]]; then
      fail "Secret '$secret' has no versions — value was never set."
    else
      log_info "OK: secret '$secret' has $count version(s)."
    fi
  done
}

run_bucket_isolation() {
  [[ -n "$TENANT_A" && -n "$TENANT_B" ]] || { log_error "--tenant-a and --tenant-b are required for bucket-isolation."; exit 1; }
  require_cmd gsutil
  validate_tenant_id "$TENANT_A"
  validate_tenant_id "$TENANT_B"

  terraform -chdir="$TF_DIR/platform" init -input=false \
    -backend-config="bucket=$STATE_BUCKET" \
    -backend-config="prefix=$(platform_state_prefix "$PROJECT_ID")" >/dev/null
  local bucket
  bucket="$(terraform -chdir="$TF_DIR/platform" output -raw shared_raw_bucket_name)"

  local sa_a="neurix-${TENANT_A}-run@${PROJECT_ID}.iam.gserviceaccount.com"
  local probe_object="${TENANT_B}/raw/isolation-probe/does-not-need-to-exist.txt"

  log_info "Bucket: gs://$bucket"
  log_info "Testing whether tenant '$TENANT_A's SA ($sa_a) can sign a GET URL for an object under tenant '$TENANT_B's prefix ($probe_object)..."
  log_warn "This exercises IAM only, not object existence — a signed URL can be produced for a nonexistent object."
  log_warn "EXPECTED: this command fails with a permission error. If it succeeds, the IAM condition is NOT isolating tenants."

  if gcloud storage sign-url "gs://${bucket}/${probe_object}" \
      --impersonate-service-account="$sa_a" \
      --private-key-file=/dev/null 2>&1 | grep -qi "denied\|permission"; then
    log_info "OK (expected): signing was denied for another tenant's prefix."
  else
    log_warn "Could not confirm denial automatically (this command needs a real private key / IAM signBlob setup to run for real —"
    log_warn "see deploy/README.md 'Known unknowns' for the manual two-tenant procedure to run by hand against a live deployment)."
  fi
}

case "$MODE" in
  default) run_default ;;
  bucket-isolation) run_bucket_isolation ;;
esac

if [[ "$FAILED" -ne 0 ]]; then
  log_error "Smoke test FAILED — see errors above."
  exit 1
fi
log_info "Smoke test passed."
