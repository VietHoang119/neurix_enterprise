# Reference copy of the GCS remote-state backend block used by both root
# modules. As with versions.tf, this file is not itself read by Terraform —
# `platform/backend.tf` and `tenant/backend.tf` each carry this same empty
# partial block, and the actual bucket + a per-tenant/per-project prefix are
# supplied at `terraform init` time via `-backend-config`, e.g.:
#
#   terraform init \
#     -backend-config="bucket=${TF_STATE_BUCKET}" \
#     -backend-config="prefix=platform/${PROJECT_ID}"
#
#   terraform init \
#     -backend-config="bucket=${TF_STATE_BUCKET}" \
#     -backend-config="prefix=tenants/${PROJECT_ID}/${TENANT_ID}"
#
# scripts/provision.sh does this for you. The bucket itself is a one-time
# manual prerequisite (see deploy/README.md) — Terraform cannot create the
# bucket it is about to store its own state in.

terraform {
  backend "gcs" {}
}
