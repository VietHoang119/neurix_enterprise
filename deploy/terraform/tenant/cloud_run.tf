# Cloud Run service — created only once enable_app = true (Terraform phase
# 2), after scripts/provision.sh has built+pushed backend_image and the
# Slack/Gemini secrets have real versions.
#
# Every setting in core/config.py's REQUIRED_IN_PRODUCTION is set below:
# DB_NAME, DB_USER, DB_PASSWORD, INSTANCE_CONNECTION_NAME, FRONTEND_URL,
# FIREBASE_PROJECT_ID, TENANT_ID. Cross-check against backend/core/config.py
# if that tuple ever changes — this list must stay in sync with it, or the
# service fails to start by design (validate_production_config()).

resource "google_cloud_run_v2_service" "backend" {
  count = var.enable_app ? 1 : 0

  project  = var.project_id
  name     = "neurix-backend-${var.tenant_id}"
  location = var.region

  labels = local.labels

  # Disables Cloud Run's own invoker-IAM check on this service
  # (google_cloud_run_v2_service.invoker_iam_disabled — confirmed present
  # on this resource in both the pinned hashicorp/google and
  # hashicorp/google-beta ~> 8.4 provider schemas, not beta-only; equivalent
  # to `gcloud run deploy --no-invoker-iam-check`). This is a property of
  # the service itself, not an IAM policy write, so it is unaffected by the
  # iam.allowedPolicyMemberDomains org policy that blocks the allUsers
  # binding below. See deploy/README.md "Public access mode" for how this
  # was verified and var.public_access_mode's description for the
  # allow_unauthenticated fallback.
  invoker_iam_disabled = var.public_access_mode == "iam_check_disabled"

  template {
    service_account = google_service_account.run.email

    scaling {
      min_instance_count = var.cloud_run_min_instances
      max_instance_count = var.cloud_run_max_instances
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.platform.sql_instance_connection_name]
      }
    }

    containers {
      image = var.backend_image

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      resources {
        limits = {
          cpu    = var.cloud_run_cpu
          memory = var.cloud_run_memory
        }
        # Must NOT be throttled: the post-Confirm background job (dedup,
        # embeddings, connection building) runs as a FastAPI background task
        # *after* the HTTP response returns, and Cloud Run's default CPU
        # throttling starves it mid-way — a real bug hit in the manual
        # deployment (docs/DEPLOYMENT_RUNBOOK.md step 14).
        cpu_idle          = false
        startup_cpu_boost = true
      }

      env {
        name  = "MODE"
        value = "production"
      }
      env {
        name  = "TENANT_ID"
        value = var.tenant_id
      }
      env {
        name  = "DB_NAME"
        value = local.db_name
      }
      env {
        name  = "DB_USER"
        value = local.db_user
      }
      env {
        name  = "INSTANCE_CONNECTION_NAME"
        value = local.platform.sql_instance_connection_name
      }
      env {
        name  = "FRONTEND_URL"
        value = var.frontend_url
      }
      env {
        name  = "FIREBASE_PROJECT_ID"
        value = local.platform.firebase_project_id
      }
      env {
        name  = "RAW_STORAGE_BUCKET"
        value = local.raw_storage_bucket
      }
      env {
        name  = "RAW_URL_TTL_SECONDS"
        value = tostring(var.raw_url_ttl_seconds)
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "GEMINI_MODEL_FALLBACKS"
        value = var.gemini_model_fallbacks
      }
      env {
        name  = "DB_POOL_SIZE"
        value = tostring(var.db_pool_size)
      }
      env {
        name  = "DB_MAX_OVERFLOW"
        value = tostring(var.db_max_overflow)
      }

      env {
        name = "DB_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SLACK_BOT_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.slack_bot_token.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "SLACK_SIGNING_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.slack_signing_secret.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "GOOGLE_API_KEY"
        value_source {
          secret_key_ref {
            secret  = local.gemini_api_key_secret_id
            version = "latest"
          }
        }
      }
    }
  }

  # Slack calls this service directly (signed requests) and browsers call it
  # with a Firebase ID token — there's no user-facing "front door" to put
  # auth in front of it, so auth is enforced inside the app itself
  # (Slack signature check, Firebase token verification), not at the Cloud
  # Run IAM layer.
  ingress = "INGRESS_TRAFFIC_ALL"

  # deploy/scripts/release.sh ships new code by running
  # `gcloud run services update --image ...` directly against Cloud Run,
  # outside Terraform (see deploy/README.md "Releasing code vs changing
  # infrastructure"). Without this, the next `terraform apply` for any
  # reason (a new tenant, a platform re-apply, an unrelated variable change)
  # would revert the service back to `var.backend_image` and silently roll
  # back the release. `client`/`client_version` are populated by gcloud on
  # every write it makes (the "Arbitrary identifier for the API client"
  # fields — confirmed against the google provider's own resource schema);
  # Terraform's config never sets them, so they'd otherwise show as
  # permanent drift on every apply too.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.db_password_accessor,
    google_secret_manager_secret_iam_member.slack_bot_token_accessor,
    google_secret_manager_secret_iam_member.slack_signing_secret_accessor,
    google_secret_manager_secret_iam_member.gemini_api_key_accessor,
    google_project_iam_member.run_sql_client,
  ]
}

# Only created in "allow_unauthenticated" mode — see var.public_access_mode.
# The default mode ("iam_check_disabled") makes the service public via the
# invoker_iam_disabled field above instead, with no allUsers IAM binding at
# all, which is what a project enforcing iam.allowedPolicyMemberDomains
# (Domain Restricted Sharing) requires.
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count = var.enable_app && var.public_access_mode == "allow_unauthenticated" ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
