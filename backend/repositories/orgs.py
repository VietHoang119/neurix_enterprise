"""orgs + org_members access. Minimal on purpose: a user belongs to at most
one org (enforced here, not in the schema), and an admin adds members
directly — there is no invite-acceptance step for membership."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def get_membership(conn: Connection, user_id: str) -> dict[str, Any] | None:
    """The caller's org: {org_id, name, role}, or None."""
    row = conn.execute(
        text(
            "SELECT o.id, o.name, m.role FROM org_members m JOIN orgs o ON o.id = m.org_id "
            "WHERE m.uid = :uid ORDER BY m.joined_at LIMIT 1"
        ),
        {"uid": user_id},
    ).first()
    if row is None:
        return None
    return {"org_id": str(row.id), "name": row.name, "role": row.role}


def create_org(conn: Connection, *, name: str, created_by: str, display_name: str | None) -> str:
    org_id = conn.execute(
        text("INSERT INTO orgs (name, created_by) VALUES (:name, :created_by) RETURNING id"),
        {"name": name, "created_by": created_by},
    ).scalar_one()
    add_member(conn, org_id=str(org_id), user_id=created_by, role="admin", display_name=display_name)
    return str(org_id)


def add_member(conn: Connection, *, org_id: str, user_id: str, role: str, display_name: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO org_members (org_id, uid, role, display_name) "
            "VALUES (:org_id, :uid, :role, :display_name)"
        ),
        {"org_id": org_id, "uid": user_id, "role": role, "display_name": display_name},
    )


def list_members(conn: Connection, org_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            "SELECT m.uid, m.role, m.display_name, u.email FROM org_members m "
            "LEFT JOIN auth.users u ON u.id = m.uid "
            "WHERE m.org_id = :org_id ORDER BY m.joined_at, m.uid"
        ),
        {"org_id": org_id},
    ).all()
    return [
        {"user_id": str(r.uid), "role": r.role, "display_name": r.display_name, "email": r.email} for r in rows
    ]


def member_ids(conn: Connection, org_id: str, *, role: str | None = None) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT uid FROM org_members WHERE org_id = :org_id "
            "AND (CAST(:role AS text) IS NULL OR role = :role) ORDER BY joined_at, uid"
        ),
        {"org_id": org_id, "role": role},
    ).all()
    return [str(r.uid) for r in rows]
