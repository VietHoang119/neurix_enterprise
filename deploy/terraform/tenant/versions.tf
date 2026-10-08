# Kept identical to ../versions.tf — see that file for why this is
# duplicated instead of shared.

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
