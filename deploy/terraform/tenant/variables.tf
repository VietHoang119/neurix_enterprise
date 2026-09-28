variable "project_id" {
  description = "GCP project this tenant is provisioned into. Must be a brand-new, empty project — never the hand-managed production project."
  type        = string

  validation {
    condition     = var.project_id != "neurix-enterprise"
    error_message = "Refusing to run: 'neurix-enterprise' is the existing hand-managed production project (no Terraform state, real tenant data). This package must only ever target a fresh, empty GCP project. Pass a different -var project_id."
  }
}

variable "region" {
  description = "GCP region. Must match the platform stack's region (where the Cloud SQL instance, Artifact Registry repo, and shared bucket live)."
  type        = string
  default     = "asia-southeast1"
}

variable "state_bucket" {
  description = "GCS bucket holding this project's Terraform state (same bucket as the platform stack, different prefix). Used to read the platform stack's outputs via terraform_remote_state."
  type        = string
}

variable "tenant_id" {
  description = "Short, stable, lowercase slug identifying this tenant (e.g. \"acme\"). Used verbatim in resource names (database, service account, secrets, Cloud Run service, Hosting site) and as the app's TENANT_ID env var / raw-storage path prefix."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,14}$", var.tenant_id))
    error_message = "tenant_id must be lowercase, start with a letter, contain only [a-z0-9-], and be 2-15 characters (kept short so it fits inside the 30-char GCP service-account account_id limit alongside the neurix-...-run prefix/suffix)."
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

variable "allow_unauthenticated" {
  description = "Grant roles/run.invoker to allUsers on the Cloud Run service. Required in practice — Slack and browser clients call the service directly and auth is enforced inside the app (Slack signature, Firebase ID token) — but can be set false to stand the service up without public access first. NOTE: on a project with the iam.allowedPolicyMemberDomains org policy restricting allUsers, this binding fails at apply time; see docs/DEPLOYMENT_RUNBOOK.md step 11 gotcha #2 for the (manual, time-boxed) org policy override."
  type        = bool
  default     = true
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

variable "labels" {
  type    = map(string)
  default = {}
}
