variable "project_id" {
  description = "GCP project this platform stack is deployed into. Must be a brand-new, empty project created for testing/tenant onboarding — never the hand-managed production project."
  type        = string

  validation {
    condition     = var.project_id != "neurix-enterprise"
    error_message = "Refusing to run: 'neurix-enterprise' is the existing hand-managed production project (no Terraform state, real tenant data). This package must only ever target a fresh, empty GCP project. Pass a different -var project_id."
  }

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must not be empty."
  }
}

variable "region" {
  description = "Default GCP region for regional resources (Cloud SQL, Artifact Registry, Cloud Build)."
  type        = string
  default     = "asia-southeast1"
}

variable "sql_instance_name" {
  description = "Name of the shared Cloud SQL instance for this project."
  type        = string
  default     = "neurix-db"
}

variable "sql_database_version" {
  description = "Cloud SQL Postgres version."
  type        = string
  default     = "POSTGRES_15"
}

variable "sql_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-g1-small"
}

variable "sql_deletion_protection" {
  description = "Cloud SQL instance deletion_protection. Left on by default; destroy.sh explicitly disables it before attempting to tear the instance down."
  type        = bool
  default     = true
}

variable "artifact_registry_repository_id" {
  description = "Artifact Registry repository id for the backend container image, shared across all tenants in this project."
  type        = string
  default     = "neurix-backend"
}

variable "shared_raw_bucket_name" {
  description = "Name of the shared raw-files GCS bucket used by tenants with dedicated_bucket = false. Must be globally unique; defaults to a name derived from project_id."
  type        = string
  default     = ""
}

variable "cloudbuild_service_account_id" {
  description = "Dedicated service account id used by `gcloud builds submit` for tenant backend image builds (avoids the default Compute Engine SA, which on new projects lacks Editor and fails source-based Cloud Build deploys — see docs/DEPLOYMENT_RUNBOOK.md step 11 gotcha #1)."
  type        = string
  default     = "neurix-cloudbuild"
}

variable "labels" {
  description = "Extra labels applied to every labelable platform resource, merged with the fixed { managed_by = \"terraform\" } label."
  type        = map(string)
  default     = {}
}
