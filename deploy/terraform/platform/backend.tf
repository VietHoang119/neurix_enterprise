# Kept identical to ../backend.tf — see that file for why this is
# duplicated instead of shared. Bucket + prefix supplied via -backend-config
# by scripts/provision.sh (prefix: platform/<project_id>).

terraform {
  backend "gcs" {}
}
