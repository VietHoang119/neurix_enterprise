from sqlalchemy import text
from sqlalchemy.engine import Connection


def find_user_id_by_email(conn: Connection, email: str) -> str | None:
    """Resolve a Slack user's email to the Postgres user_id created when
    they signed in to the web app (core/auth.py upserts auth.users)."""
    row = conn.execute(
        text(
            "SELECT id FROM auth.users WHERE lower(email) = lower(:email) "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"email": email},
    ).first()
    return str(row[0]) if row else None
