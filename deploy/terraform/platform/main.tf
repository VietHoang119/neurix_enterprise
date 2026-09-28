locals {
  labels = merge(
    { managed_by = "terraform", component = "neurix-platform" },
    var.labels,
  )

  # A GCS bucket name must be globally unique across all of GCP, not just
  # this project — derive a default from project_id when the caller didn't
  # pin one explicitly.
  shared_raw_bucket_name = var.shared_raw_bucket_name != "" ? var.shared_raw_bucket_name : "${var.project_id}-neurix-raw"
}

# ---------------------------------------------------------------------------
# APIs — enabled once per project. Every one of these is exercised somewhere
# in docs/DEPLOYMENT_RUNBOOK.md; listed there under the step that first
# needed it.
# ---------------------------------------------------------------------------
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",              # Cloud Run
    "sqladmin.googleapis.com",         # Cloud SQL Admin
    "secretmanager.googleapis.com",    # Secret Manager
    "storage.googleapis.com",          # Cloud Storage
    "artifactregistry.googleapis.com", # Artifact Registry
    "iamcredentials.googleapis.com",   # IAM Credentials (signBlob for signed URLs)
    "firebase.googleapis.com",         # Firebase
    "firebasehosting.googleapis.com",  # Firebase Hosting
    "cloudbuild.googleapis.com",       # Cloud Build
    "cloudresourcemanager.googleapis.com",
  ])

  project = var.project_id
  service = each.value

  disable_dependent_services = false
  disable_on_destroy         = false
}

# ---------------------------------------------------------------------------
# Cloud SQL — one shared instance per project; databases/users are created
# per tenant in terraform/tenant.
# ---------------------------------------------------------------------------
resource "random_password" "sql_root" {
  length  = 32
  special = false # Cloud SQL root password: keep it in the URL/CLI-safe charset.
}

resource "google_sql_database_instance" "main" {
  project             = var.project_id
  name                = var.sql_instance_name
  region              = var.region
  database_version    = var.sql_database_version
  deletion_protection = var.sql_deletion_protection

  root_password = random_password.sql_root.result

  settings {
    tier = var.sql_tier

    backup_configuration {
      enabled = true
    }

    user_labels = local.labels
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "sql_root_password" {
  project   = var.project_id
  secret_id = "sql-root-password"

  replication {
    auto {}
  }

  labels     = local.labels
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "sql_root_password" {
  secret      = google_secret_manager_secret.sql_root_password.id
  secret_data = random_password.sql_root.result
}

# ---------------------------------------------------------------------------
# Artifact Registry — one shared repository for backend images across all
# tenants in this project (images are tagged per tenant/build).
# ---------------------------------------------------------------------------
resource "google_artifact_registry_repository" "backend" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_registry_repository_id
  format        = "DOCKER"
  description   = "Neurix Enterprise backend images, shared across tenants in this project."

  labels     = local.labels
  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Dedicated Cloud Build service account for `gcloud builds submit`.
# Avoids the default Compute Engine SA, which on newer projects no longer
# auto-gets Editor and fails source-based builds with a 403 on the
# run-sources-... / cloudbuild staging bucket (docs/DEPLOYMENT_RUNBOOK.md
# step 11, gotcha #1).
# ---------------------------------------------------------------------------
resource "google_service_account" "cloudbuild" {
  project      = var.project_id
  account_id   = var.cloudbuild_service_account_id
  display_name = "Neurix tenant backend builds (Cloud Build)"
}

resource "google_project_iam_member" "cloudbuild_ar_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.cloudbuild.email}"
}

resource "google_project_iam_member" "cloudbuild_logs_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.cloudbuild.email}"
}

resource "google_project_iam_member" "cloudbuild_storage_viewer" {
  # Cloud Build reads the uploaded source archive from a GCS staging bucket.
  project = var.project_id
  role    = "roles/storage.objectViewer"
  member  = "serviceAccount:${google_service_account.cloudbuild.email}"
}

# ---------------------------------------------------------------------------
# Shared raw-files bucket — used by tenants with dedicated_bucket = false,
# scoped per tenant via an IAM condition on the object prefix (see
# terraform/tenant/storage.tf). UNVERIFIED: see deploy/README.md "Known
# unknowns" for the two-tenant signed-URL isolation test to run before
# relying on this for more than one tenant.
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "shared_raw" {
  project                     = var.project_id
  name                        = local.shared_raw_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  labels     = local.labels
  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Firebase — attaches Firebase to the GCP project. The default Hosting site
# this creates is not used directly; each tenant gets its own named Hosting
# site (terraform/tenant/hosting.tf).
# ---------------------------------------------------------------------------
resource "google_firebase_project" "default" {
  provider   = google-beta
  project    = var.project_id
  depends_on = [google_project_service.apis]
}
