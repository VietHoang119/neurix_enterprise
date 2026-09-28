# Platform facts (Cloud SQL instance, Artifact Registry repo, shared raw
# bucket, Cloud Build SA, Firebase project) this tenant module needs, sourced
# one of two ways depending on platform_mode:
#
#   "remote_state" (default) — read every value from the platform/ root
#   module's own Terraform state via terraform_remote_state. This is the
#   normal flow: platform/ and tenant/ target the same (non-production) GCP
#   project and both are Terraform-managed, in that order.
#
#   "external" — accept the same facts as plain variables instead, and never
#   read any platform Terraform state at all. This exists for exactly one
#   narrow case: provisioning a tenant against a platform stack that was NOT
#   created by this Terraform package — concretely, the hand-managed
#   production project (`neurix-enterprise`), which has real tenant data and
#   no Terraform state for its platform-level resources. See
#   deploy/README.md, "Testing a tenant on the existing production
#   platform". platform/ itself is never applied, planned, or read in this
#   mode — scripts/lib.sh's guard_not_production still refuses it
#   unconditionally for any platform/-touching operation.
data "terraform_remote_state" "platform" {
  count = var.platform_mode == "remote_state" ? 1 : 0

  backend = "gcs"
  config = {
    bucket = var.state_bucket
    prefix = "platform/${var.project_id}"
  }
}

locals {
  platform = var.platform_mode == "remote_state" ? {
    sql_instance_name                = data.terraform_remote_state.platform[0].outputs.sql_instance_name
    sql_instance_connection_name     = data.terraform_remote_state.platform[0].outputs.sql_instance_connection_name
    sql_root_password_secret_id      = data.terraform_remote_state.platform[0].outputs.sql_root_password_secret_id
    artifact_registry_repository     = data.terraform_remote_state.platform[0].outputs.artifact_registry_repository
    cloudbuild_service_account_email = data.terraform_remote_state.platform[0].outputs.cloudbuild_service_account_email
    shared_raw_bucket_name           = data.terraform_remote_state.platform[0].outputs.shared_raw_bucket_name
    firebase_project_id              = data.terraform_remote_state.platform[0].outputs.firebase_project_id
    } : {
    sql_instance_name            = var.external_sql_instance_name
    sql_instance_connection_name = var.external_sql_instance_connection_name
    # No known Secret Manager secret holds the production instance's root/
    # admin password — it was never created by this Terraform package, and
    # guessing a secret name for a production credential is exactly the kind
    # of mistake this handoff asks to avoid. Left empty on purpose:
    # scripts/provision.sh detects platform_mode = "external" and prompts
    # the operator for the password interactively instead of reading it
    # from Secret Manager.
    sql_root_password_secret_id      = ""
    artifact_registry_repository     = var.external_artifact_registry_repository
    cloudbuild_service_account_email = var.external_cloudbuild_service_account_email
    shared_raw_bucket_name           = var.external_shared_raw_bucket_name
    firebase_project_id              = var.external_firebase_project_id
  }
}

# ---------------------------------------------------------------------------
# platform_mode = "external" safety net: fail plan/apply up front, with a
# clear message naming exactly what's missing, if a required external_*
# input was left unset — instead of failing later with an empty string
# silently used as a Cloud SQL instance name, secret id, etc.
# ---------------------------------------------------------------------------
locals {
  external_mode_required = var.platform_mode == "external" ? {
    external_sql_instance_name            = var.external_sql_instance_name
    external_sql_instance_connection_name = var.external_sql_instance_connection_name
    external_artifact_registry_repository = var.external_artifact_registry_repository
    external_gemini_api_key_secret_id     = var.external_gemini_api_key_secret_id
    external_firebase_project_id          = var.external_firebase_project_id
  } : {}

  external_mode_missing = [for k, v in local.external_mode_required : k if v == ""]

  # dedicated_bucket = true doesn't need the shared bucket name at all, so
  # this one is checked separately rather than being unconditionally
  # required above.
  external_mode_missing_bucket = (
    var.platform_mode == "external" && !var.dedicated_bucket && var.external_shared_raw_bucket_name == ""
  ) ? ["external_shared_raw_bucket_name"] : []
}

resource "terraform_data" "external_mode_guard" {
  input = var.platform_mode

  lifecycle {
    precondition {
      condition     = length(local.external_mode_missing) == 0 && length(local.external_mode_missing_bucket) == 0
      error_message = "platform_mode = \"external\" requires: ${join(", ", concat(local.external_mode_missing, local.external_mode_missing_bucket))}."
    }
  }
}

# ---------------------------------------------------------------------------
# Production-project guard, Terraform-side. scripts/lib.sh's
# guard_tenant_production_opt_in (env var opt-in + typed confirmation) is the
# primary, interactive gate for running tenant/ against production — this is
# defense-in-depth for a stray direct `terraform apply` that bypasses
# scripts/provision.sh entirely. tenant/ may target the production project
# ONLY when platform_mode = "external": remote_state mode assumes a
# Terraform-managed platform stack, which production does not have (its
# platform-level resources were created by hand). This carve-out is unique
# to tenant/ — platform/variables.tf's own refusal of "neurix-enterprise" has
# no override of any kind, by design (see deploy/README.md "Hard safety
# rule").
# ---------------------------------------------------------------------------
resource "terraform_data" "production_project_guard" {
  input = var.project_id

  lifecycle {
    precondition {
      condition     = var.project_id != "neurix-enterprise" || var.platform_mode == "external"
      error_message = "Refusing: project_id = \"neurix-enterprise\" (the hand-managed production project) is only allowed for tenant/ when platform_mode = \"external\". remote_state mode assumes a Terraform-managed platform stack that production does not have. platform/ never allows this project under any mode."
    }
  }
}
