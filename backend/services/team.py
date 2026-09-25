"""Team (org) membership rules, shared by the Slack commands (`/neurix team
create | invite | list`) and the web Team page. Callers resolve who the
people are (Slack: via the Slack profile's email; web: the signed-in uid and
a typed email) and run these in one transaction; the rules live only here.

A user belongs to at most one team, and a team always keeps an admin.
"""

from typing import Any

from sqlalchemy.engine import Connection

from repositories import orgs as orgs_repo
from repositories import users as users_repo

NOT_IN_TEAM = "You're not in a team yet."
NOT_ADMIN = "Only a team admin can do that."
NOT_SIGNED_IN = (
    "This person hasn't signed in to the Neurix web app yet. "
    "Ask them to sign in once with this email, then invite them again."
)


class TeamError(ValueError):
    """A user-facing reason a team change can't be made."""


def find_invitee(conn: Connection, email: str) -> str | None:
    """The user_id behind an email, or None if they never signed in. The
    one email -> user_id lookup, also behind Slack's resolve_user_id."""
    return users_repo.find_user_id_by_email(conn, email.strip())


def create_team(conn: Connection, *, user_id: str, name: str, display_name: str | None) -> tuple[dict[str, Any], bool]:
    """-> (membership, created). An existing membership is returned as-is
    (created=False): one team per person."""
    existing = orgs_repo.get_membership(conn, user_id)
    if existing:
        return existing, False
    orgs_repo.create_org(conn, name=name, created_by=user_id, display_name=display_name)
    return orgs_repo.get_membership(conn, user_id), True


def list_team(conn: Connection, user_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """-> (membership, members); each member carries `shared_notes`, their
    count of notes in the team space."""
    membership = orgs_repo.get_membership(conn, user_id)
    if not membership:
        return None, []
    counts = orgs_repo.team_share_counts(conn, membership["org_id"])
    members = orgs_repo.list_members(conn, membership["org_id"])
    return membership, [{**m, "shared_notes": counts.get(m["user_id"], 0)} for m in members]


def _admin_membership(conn: Connection, user_id: str) -> dict[str, Any]:
    membership = orgs_repo.get_membership(conn, user_id)
    if not membership:
        raise TeamError(NOT_IN_TEAM)
    if membership["role"] != "admin":
        raise TeamError(NOT_ADMIN)
    return membership


def add_member(
    conn: Connection, *, admin_id: str, member_id: str, display_name: str | None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Add `member_id` to the admin's team as a member.
    -> (admin's membership, the member's existing membership or None if added)."""
    membership = _admin_membership(conn, admin_id)
    existing = orgs_repo.get_membership(conn, member_id)
    if existing:
        return membership, existing
    orgs_repo.add_member(conn, org_id=membership["org_id"], user_id=member_id, role="member", display_name=display_name)
    return membership, None


def remove_member(conn: Connection, *, admin_id: str, member_id: str) -> dict[str, Any]:
    """Remove a member from the admin's team; the team never ends up with
    no admin. Their notes already in the team space stay there (a share is
    never undone). -> the removed member row."""
    membership = _admin_membership(conn, admin_id)
    members = orgs_repo.list_members(conn, membership["org_id"])
    member = next((m for m in members if m["user_id"] == member_id), None)
    if member is None:
        raise TeamError("That person isn't in your team.")
    if member["role"] == "admin" and sum(m["role"] == "admin" for m in members) == 1:
        raise TeamError("You can't remove the team's only admin.")
    orgs_repo.remove_member(conn, org_id=membership["org_id"], user_id=member_id)
    return member
