#!/usr/bin/env bash
# Shared helpers for deploy/scripts/*.sh. Sourced, not executed directly.
#
#   source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# Every caller is expected to have `set -euo pipefail` itself; this file
# does the same so it is also safe if ever run stand-alone.
set -euo pipefail

# The one project this package must never touch. Created by hand, no
# Terraform state, real tenant data — see deploy/README.md "Hard safety
# rule". Every script calls guard_not_production before doing anything else.
readonly NEURIX_PRODUCTION_PROJECT_ID="neurix-enterprise"

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

# tf_state_prefix_platform <project_id>
platform_state_prefix() {
  printf 'platform/%s' "$1"
}

# tf_state_prefix_tenant <project_id> <tenant_id>
tenant_state_prefix() {
  printf 'tenants/%s/%s' "$1" "$2"
}
