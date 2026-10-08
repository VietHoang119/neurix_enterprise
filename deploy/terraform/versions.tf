# Reference copy of the provider/version pins used by both root modules.
#
# Terraform has no "include" mechanism for root-module files, and
# `platform/` and `tenant/` are separate root modules (each is `terraform
# init`'d and applied on its own, with its own state). So this file is not
# itself used by `terraform init/apply` — it documents the pins that are
# duplicated verbatim into `platform/versions.tf` and `tenant/versions.tf`.
# If you change one, change all three together.

terraform {
  required_version = ">= 1.6.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.4"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 8.4"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}
