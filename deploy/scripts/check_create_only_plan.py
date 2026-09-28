#!/usr/bin/env python3
"""Fails if a Terraform plan contains any resource_change whose actions are
anything other than create, no-op, or read.

Used by scripts/provision.sh's external-platform-mode safety net (see
deploy/README.md "Testing a tenant on the existing production platform") to
guarantee that a `terraform apply` against a platform stack that already
exists (production or otherwise) can only ever create new, tenant-suffixed
resources — never update, replace, or delete anything already there. Any
"update", "delete", or "replace" (which Terraform represents as the action
pair ["delete", "create"] or ["create", "delete"]) fails the check.

Takes plan JSON, i.e. the output of `terraform show -json <planfile>` — not
the binary plan file itself.

Usage:
    terraform show -json tfplan.binary | python3 check_create_only_plan.py
    python3 check_create_only_plan.py tfplan.json
"""

import json
import sys

ALLOWED_ACTIONS = {"no-op", "create", "read"}


def check(plan: dict) -> list[tuple[str, list[str]]]:
    """Returns a list of (address, actions) for every resource_change whose
    actions include something outside ALLOWED_ACTIONS."""
    violations = []
    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        if set(actions) - ALLOWED_ACTIONS:
            violations.append((rc.get("address", "<unknown>"), actions))
    return violations


def main() -> int:
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            plan = json.load(f)
    else:
        plan = json.load(sys.stdin)

    violations = check(plan)

    if violations:
        print("REFUSING: plan contains non-create changes:", file=sys.stderr)
        for address, actions in violations:
            print(f"  {address}: {actions}", file=sys.stderr)
        print(
            "External platform mode only ever creates new, tenant-suffixed resources — "
            "an update, replace, or delete here would touch something that already exists. "
            "Aborting before `terraform apply`.",
            file=sys.stderr,
        )
        return 1

    total = len(plan.get("resource_changes", []))
    print(f"OK: plan contains only create/no-op/read actions ({total} resource(s) checked).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
