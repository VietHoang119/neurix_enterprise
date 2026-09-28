# Raw-files storage. Either a dedicated bucket per tenant, or the shared
# platform bucket with the runtime SA's grant scoped to this tenant's
# object prefix via an IAM Condition. Either way, the app's env var is
# RAW_STORAGE_BUCKET (bucket name only) and the app itself prefixes object
# paths with TENANT_ID (services/raw_storage.py) — Terraform never encodes
# the prefix into the bucket name for the shared case.
#
# UNVERIFIED (see deploy/README.md "Known unknowns"): it has not been
# confirmed end-to-end that the prefix-scoped IAM condition below still lets
# the runtime SA sign a GET URL (IAM signBlob, see services/raw_storage.py)
# for an object under its own prefix, and that it is denied for an object
# under another tenant's prefix. Signed URLs are a distinct permission model
# from direct API calls — a condition that correctly blocks direct
# read/write may not equally constrain what can be signed. Run the two-
# tenant test in the README before trusting this for isolation.

resource "google_storage_bucket" "dedicated" {
  count = var.dedicated_bucket ? 1 : 0

  project                     = var.project_id
  name                        = "neurix-raw-${var.tenant_id}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  labels = local.labels
}

resource "google_storage_bucket_iam_member" "dedicated_object_admin" {
  count = var.dedicated_bucket ? 1 : 0

  bucket = google_storage_bucket.dedicated[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"
}

resource "google_storage_bucket_iam_member" "shared_object_admin_scoped_to_prefix" {
  count = var.dedicated_bucket ? 0 : 1

  bucket = local.platform.shared_raw_bucket_name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.run.email}"

  condition {
    title       = "neurix-tenant-${var.tenant_id}-prefix-only"
    description = "Limit ${var.tenant_id}'s runtime SA to objects under its own ${var.tenant_id}/ prefix in the shared raw bucket. UNVERIFIED for signed URLs — see deploy/README.md."
    expression  = "resource.name.startsWith(\"projects/_/buckets/${local.platform.shared_raw_bucket_name}/objects/${var.tenant_id}/\")"
  }
}

locals {
  raw_storage_bucket = var.dedicated_bucket ? google_storage_bucket.dedicated[0].name : local.platform.shared_raw_bucket_name
}
