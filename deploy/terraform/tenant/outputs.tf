output "tenant_id" {
  value = var.tenant_id
}

output "db_name" {
  value = local.db_name
}

output "db_user" {
  value = local.db_user
}

output "db_password_secret_id" {
  description = "Secret Manager secret id holding this tenant's app DB user password."
  value       = google_secret_manager_secret.db_password.secret_id
}

output "sql_root_password_secret_id" {
  description = "Passthrough of the platform's root password secret id — the migration runner (phase 2 of provisioning) connects as postgres, not as the app user."
  value       = local.platform.sql_root_password_secret_id
}

output "sql_instance_connection_name" {
  value = local.platform.sql_instance_connection_name
}

output "run_service_account_email" {
  value = google_service_account.run.email
}

output "slack_bot_token_secret_id" {
  value = google_secret_manager_secret.slack_bot_token.secret_id
}

output "slack_signing_secret_secret_id" {
  value = google_secret_manager_secret.slack_signing_secret.secret_id
}

output "gemini_api_key_secret_id" {
  value = local.gemini_api_key_secret_id
}

output "firebase_project_id" {
  value = local.platform.firebase_project_id
}

output "raw_storage_bucket" {
  value = local.raw_storage_bucket
}

output "artifact_registry_repository" {
  value = local.platform.artifact_registry_repository
}

output "cloudbuild_service_account_email" {
  value = local.platform.cloudbuild_service_account_email
}

output "cloud_run_url" {
  value = var.enable_app ? google_cloud_run_v2_service.backend[0].uri : null
}

output "hosting_site_id" {
  value = var.enable_app ? google_firebase_hosting_site.tenant[0].site_id : null
}

output "hosting_default_url" {
  value = var.enable_app ? google_firebase_hosting_site.tenant[0].default_url : null
}
