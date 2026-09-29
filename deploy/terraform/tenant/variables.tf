variable "project_id" {
  description = "GCP project this tenant is provisioned into. Normally a brand-new, empty project — never the hand-managed production project UNLESS platform_mode = \"external\" (see platform_source.tf's production_project_guard and deploy/README.md \"Testing a tenant on the existing production platform\"). platform/ itself never allows the production project under any mode — see platform/variables.tf, which has no such carve-out."
  type        = string

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must not be empty."
  }
}

variable "region" {
  description = "GCP region. Must match the platform stack's region (where the Cloud SQL instance, Artifact Registry repo, and shared bucket live)."
  type        = string
  default     = "asia-southeast1"
}

variable "state_bucket" {
  description = "GCS bucket holding this project's Terraform state (same bucket as the platform stack, different prefix). Used to read the platform stack's outputs via terraform_remote_state when platform_mode = \"remote_state\", and to hold this tenant's own state either way."
  type        = string
}

# ---------------------------------------------------------------------------
# External platform mode — see platform_source.tf for the full explanation.
# These variables are only read when platform_mode = "external"; they have
# no effect (and no validation requirement) in the default "remote_state"
# mode, so the normal flow is unaffected by their existence.
# ---------------------------------------------------------------------------
variable "platform_mode" {
  description = "How this module discovers its shared platform stack's identifiers. \"remote_state\" (default): read every platform output via terraform_remote_state — the normal flow, platform/ and tenant/ both Terraform-managed in the same project. \"external\": accept the platform's identifiers as plain external_* variables instead, and never read any platform Terraform state. See deploy/README.md \"Testing a tenant on the existing production platform\"."
  type        = string
  default     = "remote_state"

  validation {
    condition     = contains(["remote_state", "external"], var.platform_mode)
    error_message = "platform_mode must be either \"remote_state\" or \"external\"."
  }
}

variable "external_sql_instance_name" {
  description = "Cloud SQL instance short name, e.g. \"neurix-enterprise-db\" (obtain via `gcloud sql instances list`). Required when platform_mode = \"external\"."
  type        = string
  default     = ""
}

variable "external_sql_instance_connection_name" {
  description = "Cloud SQL instance connection name, PROJECT:REGION:INSTANCE, e.g. \"neurix-enterprise:asia-southeast1:neurix-enterprise-db\" (the `connectionName` field from `gcloud sql instances describe`). Required when platform_mode = \"external\"."
  type        = string
  default     = ""
}

variable "external_artifact_registry_repository" {
  description = "Full Artifact Registry repository prefix to push the tenant's backend image to, REGION-docker.pkg.dev/PROJECT/REPO (an existing, already-created repository — see deploy/README.md for the read-only `gcloud artifacts repositories list` lookup). Required when platform_mode = \"external\"."
  type        = string
  default     = ""
}

variable "external_shared_raw_bucket_name" {
  description = "Name of the existing shared raw-files GCS bucket. Required when platform_mode = \"external\" and dedicated_bucket = false; not read at all when dedicated_bucket = true."
  type        = string
  default     = ""
}

variable "external_gemini_api_key_secret_id" {
  description = "Secret Manager secret id of an EXISTING Gemini API key secret. In external mode this module only grants this tenant's runtime service account roles/secretmanager.secretAccessor on it — it never creates, modifies, or adds a version to this secret. Required when platform_mode = \"external\"."
  type        = string
  default     = ""
}

variable "external_firebase_project_id" {
  description = "Firebase project id already linked to the existing GCP project (matches project_id for a project that has already run `firebase projects:addfirebase` / has google_firebase_project applied by hand). Required when platform_mode = \"external\"."
  type        = string
  default     = ""
}

variable "external_cloudbuild_service_account_email" {
  description = "Optional: email of an existing Cloud Build service account for scripts/provision.sh's migrate phase to pass to `gcloud builds submit --service-account`. Not read by any Terraform resource in this module (passed through as an output only). If left empty, provision.sh omits --service-account and the build uses the project's default Cloud Build service account instead."
  type        = string
  default     = ""
}

variable "external_build_staging_bucket" {
  description = "Optional: name (no gs:// prefix) of an existing Cloud Build source-staging bucket for scripts/provision.sh's migrate phase to pass as --gcs-source-staging-dir=gs://<name>/source. Not read by any Terraform resource in this module (passed through as an output only). Production does not have the dedicated bucket platform/ creates; if left empty, provision.sh omits --gcs-source-staging-dir and requests CLOUD_LOGGING_ONLY against the project's default bucket (untested with a custom service account — see deploy/README.md)."
  type        = string
  default     = ""
}

variable "tenant_id" {
  description = "Short, stable, lowercase slug identifying this tenant (e.g. \"acme\"). Used verbatim in resource names (database, service account, secrets, Cloud Run service, Hosting site) and as the app's TENANT_ID env var / raw-storage path prefix."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}$", var.tenant_id))
    error_message = "tenant_id must be lowercase, start with a letter, contain only [a-z0-9-], and be 2-15 characters (kept short so it fits inside the 30-char GCP service-account account_id limit alongside the neurix-...-run prefix/suffix)."
  }
}

variable "hosting_site_id" {
  description = "Optional override for the Firebase Hosting site ID (becomes https://<id>.web.app). Empty (default) uses the computed \"<tenant_id>-<8-hex-of-sha256(project_id)>\". Hosting site IDs are globally unique across all of Firebase, so set this by hand if the computed default collides (Firebase reports that as a generic `Invalid name` error). 4-30 chars, lowercase letters/digits/hyphens, not starting or ending with a hyphen. Immutable once created — changing it later replaces the site. See deploy/README.md \"Firebase Hosting site ID collisions\"."
  type        = string
  default     = ""

  validation {
    condition     = var.hosting_site_id == "" || can(regex("^[a-z0-9][a-z0-9-]{2,28}[a-z0-9]$", var.hosting_site_id))
    error_message = "hosting_site_id must be empty (use the computed default) or 4-30 characters of lowercase letters, digits and hyphens, not starting or ending with a hyphen."
  }
}

variable "dedicated_bucket" {
  description = "When true, create a dedicated neurix-raw-<tenant> bucket for this tenant instead of using the shared platform bucket with an IAM-condition-scoped prefix grant. See deploy/README.md \"Known unknowns\" for the unverified isolation caveat on the shared-bucket path."
  type        = bool
  default     = false
}

variable "enable_app" {
  description = "Phase gate. False (default, Terraform phase 1): create every tenant resource except Cloud Run and the Firebase Hosting site. True (Terraform phase 2, run after scripts/provision.sh has built+pushed the backend image and the Slack secrets have real values): also create Cloud Run and Hosting."
  type        = bool
  default     = false
}

variable "backend_image" {
  description = "Full Artifact Registry image reference (e.g. REGION-docker.pkg.dev/PROJECT/REPO/backend:TAG) to deploy to Cloud Run. Required when enable_app = true; ignored otherwise."
  type        = string
  default     = ""

  validation {
    condition     = var.enable_app == false || length(var.backend_image) > 0
    error_message = "backend_image must be set (to the image built by scripts/provision.sh's migrate-and-build phase) when enable_app = true."
  }
}

variable "frontend_url" {
  description = "Public URL of this tenant's frontend (Firebase Hosting URL), used for CORS (FRONTEND_URL) on the backend."
  type        = string
  default     = ""

  validation {
    condition     = var.enable_app == false || length(var.frontend_url) > 0
    error_message = "frontend_url must be set when enable_app = true (Cloud Run's FRONTEND_URL / CORS origin)."
  }
}

variable "gemini_model" {
  type    = string
  default = "gemini-3.6-flash"
}

variable "gemini_model_fallbacks" {
  type    = string
  default = "gemini-3.6-flash,gemini-3.5-flash"
}

# ---------------------------------------------------------------------------
# Public access. Slack and the browser frontend both call this service
# directly (there's no front door to gate at) and auth is enforced inside
# the app (Slack signature check, Firebase ID token — see cloud_run.tf's
# `ingress` comment), so the service is normally left reachable without
# per-caller Cloud Run IAM. There are two, mutually exclusive ways to do
# that; see cloud_run.tf and deploy/README.md "Public access mode" for the
# full explanation of both and why the default is what it is.
# ---------------------------------------------------------------------------
variable "public_access_mode" {
  description = "Mechanism used to make the Cloud Run service reachable without per-caller IAM, when enable_app = true. \"iam_check_disabled\" (default): sets the service's own invoker_iam_disabled = true (google_cloud_run_v2_service field; equivalent to `gcloud run deploy --no-invoker-iam-check`) — no allUsers IAM binding is created, so this is unaffected by the iam.allowedPolicyMemberDomains org policy (Domain Restricted Sharing), which blocks granting roles/run.invoker to allUsers. \"allow_unauthenticated\": grants roles/run.invoker to allUsers via google_cloud_run_v2_service_iam_member instead — the original mechanism; use it on a project confirmed not to enforce that policy against allUsers, or where the org has granted an explicit exception. See deploy/README.md \"Public access mode\" for how each was verified and the one org-level policy (constraints/run.managed.requireInvokerIam) that can still block the iam_check_disabled path."
  type        = string
  default     = "iam_check_disabled"

  validation {
    condition     = contains(["iam_check_disabled", "allow_unauthenticated"], var.public_access_mode)
    error_message = "public_access_mode must be either \"iam_check_disabled\" or \"allow_unauthenticated\"."
  }
}

variable "cloud_run_cpu" {
  type    = string
  default = "1"
}

variable "cloud_run_memory" {
  type    = string
  default = "512Mi"
}

variable "cloud_run_min_instances" {
  type    = number
  default = 0
}

variable "cloud_run_max_instances" {
  type    = number
  default = 3
}

variable "raw_url_ttl_seconds" {
  type    = number
  default = 600
}

# Matches backend/core/config.py's Settings.DB_POOL_SIZE / DB_MAX_OVERFLOW
# defaults (5 / 10, i.e. up to 15 connections per Cloud Run instance). Needs
# to be tunable per deployment: several Cloud Run instances against one
# shared, small Cloud SQL tier (e.g. db-g1-small on the production instance
# in external platform mode) can exhaust max_connections long before CPU or
# memory becomes the bottleneck — see terraform/tenants/test1.tfvars.example
# for conservative values used there.
variable "db_pool_size" {
  description = "SQLAlchemy pool_size passed to the backend as DB_POOL_SIZE."
  type        = number
  default     = 5
}

variable "db_max_overflow" {
  description = "SQLAlchemy max_overflow passed to the backend as DB_MAX_OVERFLOW."
  type        = number
  default     = 10
}

variable "labels" {
  type    = map(string)
  default = {}
}
