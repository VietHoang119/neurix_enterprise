#!/usr/bin/env python3
"""Terraform plan safety net with two modes.

Takes plan JSON, i.e. the output of `terraform show -json <planfile>` — not
the binary plan file itself.

Usage:
    terraform show -json tfplan.binary | python3 check_create_only_plan.py
    python3 check_create_only_plan.py tfplan.json
    python3 check_create_only_plan.py --mode platform-reapply tfplan.json
    terraform show -json tfplan.binary | python3 check_create_only_plan.py --mode platform-reapply

Modes:

  create-only (default)
    Fails if a plan contains any resource_change whose actions are anything
    other than create, no-op, or read. Any "update", "delete", or "replace"
    (which Terraform represents as the action pair ["delete", "create"] or
    ["create", "delete"]) fails the check.

    Used by scripts/provision.sh's external-platform-mode safety net (see
    deploy/README.md "Testing a tenant on the existing production
    platform") to guarantee that a `terraform apply` against a platform
    stack that already exists (production or otherwise) can only ever
    create new, tenant-suffixed resources — never update, replace, or
    delete anything already there.

  platform-reapply
    For re-applying deploy/terraform/platform against a platform stack that
    is already live and serving tenants (see deploy/README.md "Releasing
    code vs changing infrastructure"). In-place updates are expected and
    fine here (e.g. bumping sql_tier, adding a label) — this mode only
    fails if the plan would delete or replace ("delete" appears anywhere in
    a resource's actions) a STATEFUL resource: the Cloud SQL instance, any
    SQL database, or a GCS bucket. A stray platform apply must never be able
    to drop the database or a bucket, even via an in-place-looking change
    that Terraform actually has to implement as a replace (e.g. changing a
    force-new attribute like region or collation on the SQL instance).
    Non-stateful resources (APIs, IAM bindings, the Artifact Registry repo,
    the Cloud Build service account, the Firebase project link) may still be
    deleted/replaced in this mode — losing one of those is inconvenient, not
    a data-loss incident, and re-applying platform/ legitimately needs the
    freedom to change them.
"""

import argparse
import json
import sys

ALLOWED_ACTIONS_CREATE_ONLY = {"no-op", "create", "read"}

# Resource types this checker treats as holding data that cannot be
# recreated from Terraform config alone. Kept as an explicit allowlist
# (rather than "everything except APIs/IAM") so a new stateful resource type
# added to platform/ later must be added here deliberately, not assumed
# covered.
STATEFUL_RESOURCE_TYPES = {
    "google_sql_database_instance",
    "google_sql_database",
    "google_storage_bucket",
}


def check_create_only(plan: dict) -> list[tuple[str, list[str]]]:
    """Returns a list of (address, actions) for every resource_change whose
    actions include something outside ALLOWED_ACTIONS_CREATE_ONLY."""
    violations = []
    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if set(actions) - ALLOWED_ACTIONS_CREATE_ONLY:
            violations.append((rc.get("address", "<unknown>"), actions))
    return violations


def check_platform_reapply(plan: dict) -> list[tuple[str, list[str]]]:
    """Returns a list of (address, actions) for every resource_change on a
    STATEFUL_RESOURCE_TYPES resource whose actions include "delete" (a plain
    delete, or the "delete"+"create" / "create"+"delete" pair Terraform uses
    to represent a replace). Updates, creates, and deletes/replaces of
    non-stateful resources are all allowed."""
    violations = []
    for rc in plan.get("resource_changes", []):
        if rc.get("type") not in STATEFUL_RESOURCE_TYPES:
            continue
        actions = rc.get("change", {}).get("actions", [])
        if "delete" in actions:
            violations.append((rc.get("address", "<unknown>"), actions))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode",
        choices=["create-only", "platform-reapply"],
        default="create-only",
        help="create-only (default): reject any non-create/no-op/read change. "
        "platform-reapply: allow in-place updates, reject only delete/replace of a stateful resource.",
    )
    parser.add_argument("planfile", nargs="?", help="Plan JSON file. Reads stdin if omitted.")
    args = parser.parse_args()

    if args.planfile:
        with open(args.planfile) as f:
            plan = json.load(f)
    else:
        plan = json.load(sys.stdin)

    if args.mode == "create-only":
        violations = check_create_only(plan)
        violation_kind = "non-create changes"
        explanation = (
            "External platform mode only ever creates new, tenant-suffixed resources — "
            "an update, replace, or delete here would touch something that already exists. "
            "Aborting before `terraform apply`."
        )
    else:
        violations = check_platform_reapply(plan)
        violation_kind = "deletes/replaces of a stateful resource"
        explanation = (
            "platform-reapply mode allows in-place updates but never a delete or replace of a "
            f"stateful resource ({', '.join(sorted(STATEFUL_RESOURCE_TYPES))}). "
            "A platform re-apply must never be able to drop the database or a bucket. "
            "Aborting before `terraform apply`."
        )

    if violations:
        print(f"REFUSING: plan contains {violation_kind}:", file=sys.stderr)
        for address, actions in violations:
            print(f"  {address}: {actions}", file=sys.stderr)
        print(explanation, file=sys.stderr)
        return 1

    total = len(plan.get("resource_changes", []))
    print(f"OK ({args.mode}): plan passes ({total} resource(s) checked).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
