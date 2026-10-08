#!/usr/bin/env bash
# Shifts 100% of a tenant's Cloud Run traffic back to its previous revision
# — no rebuild, no image push, no Terraform. The counterpart to
# scripts/release.sh; see deploy/README.md "Releasing code vs changing
# infrastructure".
#
# Usage:
#   rollback.sh --project-id ID [--region REGION] <tenant_id>
#
# Self-sufficient: it determines "the previous revision" itself, live, from
# Cloud Run's own revision history for the tenant's service (the two most
# recently created revisions, newest first; the older of the two is the
# rollback target) — it does not depend on anything scripts/release.sh
# recorded or printed, so it works even run from a different machine or
# session than the release that needs rolling back. Refuses if the service
# doesn't exist, or has fewer than two revisions (nothing to roll back to).
#
# This only moves traffic — it does not touch the RELEASE env var or
# release= label scripts/release.sh sets (those describe the image baked
# into each revision, which rollback doesn't change), and it does not run
# any database migration (rolling back code is not the same operation as
# rolling back a schema — see deploy/README.md's expand/contract rule: a
# migration that shipped with the release being rolled back must already
# have been safe for the OLD code to run against, which is the entire point
# of that rule).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=./lib.sh
source "$SCRIPT_DIR/lib.sh"

PROJECT_ID=""
REGION="asia-southeast1"
TENANT_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-id) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --) shift; break ;;
    -*) log_error "Unknown flag: $1"; exit 1 ;;
    *)
      if [[ -n "$TENANT_ID" ]]; then
        log_error "Only one tenant_id may be given (got '$TENANT_ID' and '$1')."
        exit 1
      fi
      TENANT_ID="$1"
      shift
      ;;
  esac
done

require_cmd gcloud

[[ -n "$PROJECT_ID" ]] || { log_error "--project-id is required."; exit 1; }
[[ -n "$TENANT_ID" ]] || { log_error "Usage: rollback.sh --project-id ID [--region REGION] <tenant_id>"; exit 1; }
validate_tenant_id "$TENANT_ID"

SERVICE="neurix-backend-${TENANT_ID}"   # must match terraform/tenant/cloud_run.tf's service name

log_info "Looking up revision history for '$SERVICE' (project=$PROJECT_ID region=$REGION)..."
if ! gcloud run services describe "$SERVICE" --project="$PROJECT_ID" --region="$REGION" >/dev/null 2>&1; then
  log_error "Cloud Run service '$SERVICE' not found in project '$PROJECT_ID', region '$REGION'."
  exit 1
fi

# Two most recently created revisions, newest first. Cloud Run revision
# names are not lexically sortable by age (they include a random suffix),
# so sort by creation timestamp, not by name.
# (Read line by line instead of `mapfile`, which needs Bash 4+.)
RECENT_REVISIONS=()
while IFS= read -r revision_line; do
  if [[ -n "$revision_line" ]]; then
    RECENT_REVISIONS+=("$revision_line")
  fi
done < <(
  gcloud run revisions list \
    --service="$SERVICE" --project="$PROJECT_ID" --region="$REGION" \
    --sort-by="~metadata.creationTimestamp" \
    --format='value(metadata.name)' \
    --limit=2
)

if [[ "${#RECENT_REVISIONS[@]}" -lt 2 ]]; then
  log_error "Refusing: '$SERVICE' has ${#RECENT_REVISIONS[@]} revision(s) — no previous revision to roll back to."
  exit 1
fi

CURRENT_REVISION="${RECENT_REVISIONS[0]}"
PREVIOUS_REVISION="${RECENT_REVISIONS[1]}"

log_info "Current revision:  $CURRENT_REVISION"
log_info "Previous revision: $PREVIOUS_REVISION"
log_warn "About to shift 100% of '$SERVICE' traffic from '$CURRENT_REVISION' back to '$PREVIOUS_REVISION'."
confirm "Type 'yes' to proceed" || { log_info "Aborted."; exit 1; }

gcloud run services update-traffic "$SERVICE" \
  --project="$PROJECT_ID" --region="$REGION" \
  --to-revisions="${PREVIOUS_REVISION}=100"

log_info "Traffic for '$SERVICE' now 100% on '$PREVIOUS_REVISION'."
log_warn "Note: '$CURRENT_REVISION' still exists (traffic-shifted, not deleted) — the RELEASE env var/label it carries no longer reflects what's actually serving traffic. Run scripts/release.sh again with a fixed image to move forward instead of leaving this rollback as the long-term state."
