# Firebase Hosting site for this tenant's frontend. Grouped into phase 2
# (enable_app) alongside Cloud Run mainly for provisioning-order simplicity:
# scripts/provision.sh's frontend-and-finish phase needs both the site to
# exist and the Cloud Run URL to bake into the frontend build
# (VITE_API_BASE_URL), so it makes sense to create them together right
# before that phase runs. Site content itself is deployed separately by
# `firebase deploy --only hosting` in that phase, not by Terraform.

locals {
  # Firebase Hosting site IDs are globally unique across ALL Firebase
  # projects worldwide (they become <site_id>.web.app), and 4-30 chars of
  # [a-z0-9-]. A bare "<tenant>-neurix" has no project-specific entropy, so
  # short, common tenant ids (acme, demo, pilot, ...) can collide with a site
  # someone else already owns. Google reports that as a generic
  # `Invalid name` 400, not a distinct ALREADY_EXISTS.
  #
  # Default: "<tenant_id>-<first 8 hex chars of sha256(project_id)>".
  #   - The project id is itself globally unique, so hashing it in gives every
  #     project its own namespace for the same tenant id.
  #   - Hashing (rather than "<project_id>-<tenant_id>") is deliberate: a
  #     GCP project id may be up to 30 chars and tenant_id up to 15, so the
  #     plain concatenation can reach 46 chars and would have to be truncated,
  #     silently dropping either project or tenant entropy. Here the length is
  #     bounded by construction: at most 15 (tenant_id, see its validation) + 1
  #     + 8 = 24 chars, always <= 30, and never truncated.
  #   - Deterministic (no random_* resource), so the ID is stable across plans.
  #   - Only [a-z0-9-]; tenant_id is already validated as lowercase, and hex
  #     digits are lowercase, so no sanitizing is needed. tenant_id must start
  #     with a letter, so the ID cannot start with a hyphen or digit.
  # This lowers collision risk; it cannot eliminate it (there is no public
  # availability check). Set var.hosting_site_id to override if it still
  # collides.
  hosting_site_id_default = "${var.tenant_id}-${substr(sha256(var.project_id), 0, 8)}"
  hosting_site_id         = var.hosting_site_id != "" ? var.hosting_site_id : local.hosting_site_id_default
}

resource "google_firebase_hosting_site" "tenant" {
  provider = google-beta
  count    = var.enable_app ? 1 : 0

  project = var.project_id
  site_id = local.hosting_site_id

  # data.terraform_remote_state.platform only exists (count=1) in the
  # default remote_state mode; terraform_data.external_mode_guard exists in
  # both modes and is a safe, always-present ordering dependency either way
  # (its own precondition already fails the whole plan early if an external
  # mode input is missing).
  depends_on = [data.terraform_remote_state.platform, terraform_data.external_mode_guard]
}
