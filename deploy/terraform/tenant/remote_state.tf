data "terraform_remote_state" "platform" {
  backend = "gcs"
  config = {
    bucket = var.state_bucket
    prefix = "platform/${var.project_id}"
  }
}

locals {
  platform = data.terraform_remote_state.platform.outputs
}
