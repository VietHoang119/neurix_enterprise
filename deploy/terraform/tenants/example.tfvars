# Example tenant tfvars — committed as a template only. Copy this to a new,
# gitignored file (e.g. terraform/tenants/acme.tfvars) per real tenant and
# fill in real values. Never commit the copy — deploy/.gitignore already
# excludes every terraform/tenants/*.tfvars except this example.
#
# Do NOT put Slack bot token / signing secret / Gemini API key values here.
# Those are added later by scripts/provision.sh directly into Secret
# Manager, never through a .tfvars file or Terraform state.

project_id   = "REPLACE_WITH_YOUR_TEST_PROJECT_ID" # never "neurix-enterprise"
region       = "asia-southeast1"
state_bucket = "REPLACE_WITH_YOUR_TF_STATE_BUCKET"

tenant_id        = "acme"
dedicated_bucket = false

# Leave enable_app at its default (false) for Terraform phase 1. Pass
# -var enable_app=true -var backend_image=... -var frontend_url=...
# explicitly for phase 2, once the image has been built and pushed and the
# Slack/Gemini secrets have real values — see deploy/README.md.

gemini_model           = "gemini-3.6-flash"
gemini_model_fallbacks = "gemini-3.6-flash,gemini-3.5-flash"
