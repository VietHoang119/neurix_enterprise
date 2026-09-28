output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "sql_instance_name" {
  value = google_sql_database_instance.main.name
}

output "sql_instance_connection_name" {
  description = "PROJECT_ID:REGION:INSTANCE — the Cloud SQL Python Connector value used as INSTANCE_CONNECTION_NAME."
  value       = google_sql_database_instance.main.connection_name
}

output "sql_root_password_secret_id" {
  description = "Secret Manager secret id holding the Cloud SQL root password. Never output the value itself — scripts read it via `gcloud secrets versions access`."
  value       = google_secret_manager_secret.sql_root_password.secret_id
}

output "artifact_registry_repository" {
  description = "REGION-docker.pkg.dev/PROJECT_ID/REPO — prefix for tenant image tags."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.backend.repository_id}"
}

output "cloudbuild_service_account_email" {
  value = google_service_account.cloudbuild.email
}

output "shared_raw_bucket_name" {
  value = google_storage_bucket.shared_raw.name
}

output "firebase_project_id" {
  value = google_firebase_project.default.project
}
