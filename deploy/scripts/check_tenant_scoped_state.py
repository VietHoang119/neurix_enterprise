#!/usr/bin/env python3
"""Refuses (exit 1) unless a Terraform state's own `tenant_id` output
matches the tenant_id given on the command line, and every resource address
in that state is one this package's terraform/tenant module can produce.

Used by deploy/scripts/destroy.sh's external-mode safety net (see
deploy/README.md "Testing a tenant on the existing production platform") to
catch a --tenant-id / --tfvars / --state-bucket mismatch before `terraform
destroy` ever runs — i.e. to confirm the state about to be destroyed really
is this tenant's own, and nothing unexpected ended up in the same state
prefix.

Takes state JSON, i.e. the output of `terraform show -json` run against the
CURRENT STATE (no planfile argument) — not a plan.

Usage:
    terraform show -json | python3 check_tenant_scoped_state.py <tenant_id>
"""

import json
import sys

# Every resource type/name this package's terraform/tenant module can ever
# create, as "<type>.<name>" (address with any [count-index]/module prefix
# stripped). Kept in sync by hand with terraform/tenant/*.tf — a resource
# address outside this set is either config drift or evidence the wrong
# state was targeted; either way destroy.sh should stop, not proceed.
KNOWN_RESOURCES = {
    "google_sql_database.tenant",
    "random_password.db_user",
    "google_sql_user.tenant",
    "google_secret_manager_secret.db_password",
    "google_secret_manager_secret_version.db_password",
    "google_secret_manager_secret.slack_bot_token",
    "google_secret_manager_secret.slack_signing_secret",
    "google_secret_manager_secret.gemini_api_key",
    "google_service_account.run",
    "google_secret_manager_secret_iam_member.db_password_accessor",
    "google_secret_manager_secret_iam_member.slack_bot_token_accessor",
    "google_secret_manager_secret_iam_member.slack_signing_secret_accessor",
    "google_secret_manager_secret_iam_member.gemini_api_key_accessor",
    "google_project_iam_member.run_sql_client",
    "google_service_account_iam_member.run_token_creator_self",
    "google_cloud_run_v2_service.backend",
    "google_cloud_run_v2_service_iam_member.public_invoker",
    "google_firebase_hosting_site.tenant",
    "google_storage_bucket.dedicated",
    "google_storage_bucket_iam_member.dedicated_object_admin",
    "google_storage_bucket_iam_member.shared_object_admin_scoped_to_prefix",
    "terraform_data.external_mode_guard",
    "terraform_data.production_project_guard",
}


def normalize_address(address: str) -> str:
    """Strips a `[0]`/`["key"]` count/for_each index suffix and any leading
    `module.x.` prefix down to "<type>.<name>"."""
    addr = address
    if "[" in addr:
        addr = addr[: addr.index("[")]
    parts = addr.split(".")
    return ".".join(parts[-2:])


def main() -> int:
    tenant_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not tenant_id:
        print("usage: check_tenant_scoped_state.py <tenant_id>  (state JSON on stdin)", file=sys.stderr)
        return 2

    state = json.load(sys.stdin)
    root_resources = state.get("values", {}).get("root_module", {}).get("resources", [])

    unknown = sorted({r["address"] for r in root_resources if normalize_address(r["address"]) not in KNOWN_RESOURCES})
    if unknown:
        print("REFUSING: state contains resource(s) this module doesn't recognize as tenant-scoped:", file=sys.stderr)
        for addr in unknown:
            print(f"  {addr}", file=sys.stderr)
        return 1

    outputs = state.get("values", {}).get("outputs", {})
    state_tenant_id = outputs.get("tenant_id", {}).get("value")
    if state_tenant_id != tenant_id:
        print(
            f"REFUSING: this state's own tenant_id output is {state_tenant_id!r}, not {tenant_id!r} — "
            "wrong --tenant-id, --tfvars, or --state-bucket.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: state is scoped to tenant '{tenant_id}' ({len(root_resources)} resource(s) checked).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
