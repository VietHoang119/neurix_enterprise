# Firebase Hosting site for this tenant's frontend. Grouped into phase 2
# (enable_app) alongside Cloud Run mainly for provisioning-order simplicity:
# scripts/provision.sh's frontend-and-finish phase needs both the site to
# exist and the Cloud Run URL to bake into the frontend build
# (VITE_API_BASE_URL), so it makes sense to create them together right
# before that phase runs. Site content itself is deployed separately by
# `firebase deploy --only hosting` in that phase, not by Terraform.

resource "google_firebase_hosting_site" "tenant" {
  provider = google-beta
  count    = var.enable_app ? 1 : 0

  project = var.project_id
  site_id = "${var.tenant_id}-neurix"

  depends_on = [data.terraform_remote_state.platform]
}
