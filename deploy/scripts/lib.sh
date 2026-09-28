#!/usr/bin/env bash
# Shared helpers for deploy/scripts/*.sh. Sourced, not executed directly.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Every caller is expected to have `set -euo pipefail` itself; this file
# does the same so it is also safe if ever run stand-alone.
set -euo pipefail

# The one project this package must never touch by default. Created by
# hand, no Terraform state for its platform-level resources, real tenant
# data — see deploy/README.md "Hard safety rule". platform/ and any
# platform-touching script step call guard_not_production, which has no
# override of any kind. tenant/-only operations may instead go through the
# layered opt-in gate in guard_tenant_production_opt_in — see
# deploy/README.md "Testing a tenant on the existing production platform".
readonly NEURIX_PRODUCTION_PROJECT_ID="neurix-enterprise"

# tenant_id values that must never be used for a tenant provisioned against
# the production project, even with the opt-in env var set — each one is
# either the production project's own defacto name, a demo/example id likely
# to already exist or be reused by habit, or a generic word ("default",
# "prod", "production", "main") someone could type without thinking. This is
# on top of, not instead of, validate_tenant_id_strict's pattern check.
readonly NEURIX_PRODUCTION_TENANT_ID_DENYLIST=(neurix neurix-demo-team default prod production main)

log_info()  { printf '[INFO]  %s\n' "$*" >&2; }
log_warn()  { printf '[WARN]  %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }

# guard_not_production <project_id>
#
# Refuses to continue if project_id is the hand-managed production project.
# Called at the top of every script in this package, before any other
# argument parsing or side effect, so a typo'd --project-id can never reach
# a gcloud/terraform command.
guard_not_production() {
  local project_id="${1:-}"
  if [[ -z "$project_id" ]]; then
    log_error "guard_not_production: no project_id given."
    exit 1
  fi
  if [[ "$project_id" == "$NEURIX_PRODUCTION_PROJECT_ID" ]]; then
    log_error "Refusing to run against '$NEURIX_PRODUCTION_PROJECT_ID'."
    log_error "That is the existing hand-managed production project: no Terraform state, real tenant data."
    log_error "This package is only for a brand-new, empty GCP project. Pass a different --project-id."
    exit 1
  fi
}

# require_cmd <name> [<name> ...]
#
# Fails fast with a clear message naming every missing tool at once, rather
# than dying partway through a phase on the first missing one.
require_cmd() {
  local missing=()
  local cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done
  if ((${#missing[@]} > 0)); then
    log_error "Missing required command(s): ${missing[*]}"
    log_error "See deploy/README.md prerequisites."
    exit 1
  fi
}

# validate_tenant_id <tenant_id>
#
# Mirrors the Terraform variable validation in terraform/tenant/variables.tf
# so a bad tenant_id fails at the shell layer with the same message, before
# ever reaching `terraform apply`.
validate_tenant_id() {
  local tenant_id="${1:-}"
  if [[ ! "$tenant_id" =~ ^[a-z][a-z0-9-]{1,14}$ ]]; then
    log_error "Invalid tenant_id: '$tenant_id'"
    log_error "Must be lowercase, start with a letter, contain only [a-z0-9-], 2-15 characters."
    exit 1
  fi
}

# confirm <prompt>
#
# Interactive yes/no gate. Returns non-zero (caller should exit) on anything
# but an explicit "yes".
confirm() {
  local prompt="${1:-Continue?}"
  local answer
  read -r -p "$prompt [type 'yes' to continue] " answer
  [[ "$answer" == "yes" ]]
}

# validate_tenant_id_strict <tenant_id>
#
# Stricter pattern used specifically by guard_tenant_production_opt_in, on
# top of (never instead of) validate_tenant_id and Terraform's own
# tenant_id variable validation (both still apply and are in practice
# narrower: lowercase, must start with a letter, 2-15 characters, vs. this
# function's lowercase+digits+hyphen, 3-20 characters). Kept as a separate,
# explicitly named check because the production handoff specified this
# exact shape for the production-project opt-in gate.
validate_tenant_id_strict() {
  local tenant_id="${1:-}"
  if [[ ! "$tenant_id" =~ ^[a-z0-9-]{3,20}$ ]]; then
    log_error "Invalid tenant_id for production-project mode: '$tenant_id'"
    log_error "Must be lowercase letters, digits, and hyphens only; 3-20 characters."
    exit 1
  fi
  local denied
  for denied in "${NEURIX_PRODUCTION_TENANT_ID_DENYLIST[@]}"; do
    if [[ "$tenant_id" == "$denied" ]]; then
      log_error "tenant_id '$tenant_id' is on the production denylist (reserved/ambiguous id) and cannot be used against the production project."
      exit 1
    fi
  done
}

# guard_tenant_production_opt_in <project_id> <tenant_id> <platform_mode>
#
# Layered gate for running a *tenant-only* operation (never platform/, never
# `provision.sh all`, never `destroy.sh platform`/`all`) against the
# production project. A no-op (returns 0 immediately) for any other
# project_id — this is deliberately NOT guard_not_production; it exists
# precisely to allow the one narrow case guard_not_production forbids
# outright. All of the following must hold, checked in this order, any
# failure exits non-zero without side effects:
#
#   1. platform_mode == "external" — remote_state mode assumes a
#      Terraform-managed platform stack, which production does not have.
#   2. NEURIX_ALLOW_PRODUCTION_PROJECT=yes is set in the environment — an
#      explicit, deliberate opt-in that can't be fat-fingered via a CLI flag
#      default.
#   3. tenant_id passes validate_tenant_id_strict (pattern + denylist).
#   4. The operator retypes tenant_id at an interactive prompt, after a loud
#      banner naming the project, and that this is production.
#
# Mirrored by terraform/tenant/platform_source.tf's
# production_project_guard precondition for the case someone runs
# `terraform apply` directly, bypassing this script entirely.
guard_tenant_production_opt_in() {
  local project_id="$1" tenant_id="$2" platform_mode="$3"

  if [[ "$project_id" != "$NEURIX_PRODUCTION_PROJECT_ID" ]]; then
    return 0
  fi

  log_warn "=================================================================="
  log_warn " PRODUCTION PROJECT: $NEURIX_PRODUCTION_PROJECT_ID"
  log_warn " A tenant-only operation was requested against the production"
  log_warn " project. This is only ever allowed in external platform mode,"
  log_warn " with an explicit opt-in. See deploy/README.md \"Testing a"
  log_warn " tenant on the existing production platform\"."
  log_warn "=================================================================="

  if [[ "$platform_mode" != "external" ]]; then
    log_error "Refusing: tenant/ operations against '$NEURIX_PRODUCTION_PROJECT_ID' require platform_mode = \"external\" (set in the tenant's .tfvars file)."
    log_error "remote_state mode assumes a Terraform-managed platform stack, which does not exist for production."
    exit 1
  fi

  if [[ "${NEURIX_ALLOW_PRODUCTION_PROJECT:-}" != "yes" ]]; then
    log_error "Refusing: set NEURIX_ALLOW_PRODUCTION_PROJECT=yes to enable tenant operations against the production project."
    exit 1
  fi

  if [[ -z "$tenant_id" ]]; then
    log_error "Refusing: no tenant_id given for a tenant operation against the production project."
    exit 1
  fi
  validate_tenant_id_strict "$tenant_id"

  log_warn "About to run a TENANT-ONLY operation against the PRODUCTION project."
  log_warn "  project:       $project_id"
  log_warn "  tenant_id:     $tenant_id"
  log_warn "  platform_mode: external"
  log_warn "This will create new, tenant-suffixed resources on the shared production Cloud SQL instance."
  local typed
  read -r -p "Type the tenant id ('$tenant_id') to confirm: " typed
  if [[ "$typed" != "$tenant_id" ]]; then
    log_error "Typed value did not match tenant_id. Aborting."
    exit 1
  fi

  log_warn "=================================================================="
  log_warn " PRODUCTION MODE ACTIVE — proceeding for tenant '$tenant_id' on '$project_id'."
  log_warn "=================================================================="
}

# guard_sql_target <project_id> <tenant_id> <instance> <db_name> <db_user>
#
# Defense-in-depth check run immediately before every SQL-touching step
# (migrations, grants, any future probe) — this is what stands between a
# bad --tenant-id/--tfvars mismatch and a GRANT or ALTER DEFAULT PRIVILEGES
# landing on 'neurix', 'postgres', or any database that isn't this tenant's
# own. Always prints instance/db/user before connecting, and requires an
# interactive typed confirmation when project_id is the production project —
# on top of, not instead of, the top-level guard_tenant_production_opt_in
# gate already passed once before the phase that calls this started.
guard_sql_target() {
  local project_id="$1" tenant_id="$2" instance="$3" db_name="$4" db_user="$5"
  local expected="neurix_${tenant_id}"

  if [[ "$db_name" != "$expected" ]]; then
    log_error "Refusing SQL step: target database '$db_name' does not match the expected tenant database '$expected'."
    log_error "This guard exists specifically to stop a migration/grant from ever landing on 'neurix', 'postgres', or any database that isn't this tenant's own."
    exit 1
  fi

  log_info "SQL target — instance: $instance | database: $db_name | user: $db_user"

  if [[ "$project_id" == "$NEURIX_PRODUCTION_PROJECT_ID" ]]; then
    log_warn "About to run a SQL step against the PRODUCTION project's Cloud SQL instance."
    confirm "Type 'yes' to confirm this SQL step against '$db_name' on '$instance'" \
      || { log_error "Aborted at SQL guard."; exit 1; }
  fi
}

# read_tfvar_string <tfvars_file> <key> [default]
#
# Extracts a simple string-typed top-level key ("key = \"value\"") from a
# .tfvars file via a plain regex — enough for the handful of scalar keys
# provision.sh/destroy.sh need to read back (platform_mode) without adding a
# full HCL parser. Comments and non-string types are not supported.
read_tfvar_string() {
  local file="$1" key="$2" default="${3:-}"
  local line value
  if [[ ! -f "$file" ]]; then
    printf '%s' "$default"
    return 0
  fi
  line="$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null | tail -n1 || true)"
  if [[ -z "$line" ]]; then
    printf '%s' "$default"
    return 0
  fi
  value="${line#*=}"
  value="$(printf '%s' "$value" | sed -E 's/^[[:space:]]*"?//; s/"?[[:space:]]*(#.*)?$//')"
  printf '%s' "$value"
}

# read_secret <prompt> <out_var_name>
#
# Hidden-input read (never echoed to the terminal, never left in shell
# history). Sets the named variable via nameref rather than printing/
# returning the value, so a secret is never captured in $(...) output.
read_secret() {
  local prompt="$1"
  local -n out_ref="$2"
  local value
  read -r -s -p "$prompt " value
  echo >&2
  # shellcheck disable=SC2034  # out_ref is a nameref into the caller's variable
  out_ref="$value"
}

# gcloud_secret_add_version <project_id> <secret_id> <value>
#
# Adds a new Secret Manager version from raw bytes with NO trailing newline.
# Always uses `printf '%s'` piped to --data-file=-, never `echo` or a
# here-string (both of those append a newline, which silently breaks the
# Slack signing-secret HMAC check — this bit a real deployment, see
# docs/DEPLOYMENT_RUNBOOK.md step 10's gotcha and this package's own
# handoff notes). Never logs the value.
gcloud_secret_add_version() {
  local project_id="$1"
  local secret_id="$2"
  local value="$3"
  printf '%s' "$value" | gcloud secrets versions add "$secret_id" \
    --project="$project_id" \
    --data-file=-
}

# guard_hosting_config_site <config_file> <site_id> <default_site>
#
# Defense-in-depth check run immediately before every tenant frontend
# deploy, right after writing the throwaway Firebase Hosting config for
# this run — mirrors guard_sql_target's pattern of re-verifying a trusted
# value (here, Terraform's own hosting_site_id output) rather than assuming
# it. Confirms (a) the config file actually contains the tenant's own site
# id, not something a bug silently swapped, and (b) that site is not the
# project's own default Hosting site (which is auto-created under the
# project id itself) — see deploy/README.md "Frontend build/deploy
# isolation" for why landing on the default site would be a problem.
guard_hosting_config_site() {
  local config_file="$1" site_id="$2" default_site="$3"

  if [[ -z "$site_id" || "$site_id" == "null" ]]; then
    log_error "guard_hosting_config_site: no site_id given."
    exit 1
  fi
  if [[ "$site_id" == "$default_site" ]]; then
    log_error "Refusing: Hosting site '$site_id' is the project's default site ('$default_site')."
    log_error "This guard exists to stop a tenant deploy from ever landing on the project's default Hosting site instead of a tenant-suffixed one."
    exit 1
  fi

  local parsed_site
  if ! parsed_site="$(python3 -c '
import json, sys
with open(sys.argv[1]) as f:
    cfg = json.load(f)
print(cfg["hosting"]["site"])
' "$config_file" 2>/dev/null)"; then
    log_error "guard_hosting_config_site: could not read hosting.site back from $config_file."
    exit 1
  fi
  if [[ "$parsed_site" != "$site_id" ]]; then
    log_error "guard_hosting_config_site: $config_file's hosting.site ('$parsed_site') does not match the expected tenant site ('$site_id')."
    exit 1
  fi
}

# guard_hosting_deploy_args <expected_config_path> <deploy argv...>
#
# Refuses to run a Firebase Hosting deploy whose argv doesn't explicitly
# pin --config to the tenant-scoped throwaway config this phase just wrote
# and verified. Exists specifically to stop a bare `firebase deploy --only
# hosting` — which would fall back to frontend/firebase.json and whatever
# frontend/.firebaserc's default site/project currently is — from ever
# being the command that actually runs for a tenant deploy.
guard_hosting_deploy_args() {
  local expected_config="$1"
  shift
  local -a args=("$@")
  local i found=0
  for ((i = 0; i < ${#args[@]}; i++)); do
    if [[ "${args[$i]}" == "--config" && "${args[$((i + 1))]:-}" == "$expected_config" ]]; then
      found=1
      break
    fi
  done
  if [[ "$found" -ne 1 ]]; then
    log_error "Refusing to run firebase deploy: --config $expected_config is not present in the deploy command (${args[*]})."
    log_error "This guard exists to stop a bare 'firebase deploy --only hosting' from ever running for a tenant deploy."
    exit 1
  fi
}

# tf_state_prefix_platform <project_id>
platform_state_prefix() {
  printf 'platform/%s' "$1"
}

# tf_state_prefix_tenant <project_id> <tenant_id>
tenant_state_prefix() {
  printf 'tenants/%s/%s' "$1" "$2"
}
