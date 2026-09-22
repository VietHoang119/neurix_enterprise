"""Firebase ID token verification + auth.users shim upsert.

Replaces neurix's Supabase JWT auth (app/middleware/auth_middleware.py). The
handoff says the decoded Firebase `uid` "becomes user_id everywhere
downstream" — but auth.users.id (and every FK into it: ai_message_feedback,
survey_response, mcp_api_keys) is typed `uuid`, and Firebase uids are ~28
char alphanumeric strings, not valid UUIDs. Inserting one as-is into a `uuid`
column fails.

So `user_id` is a deterministic uuid5 of the Firebase uid (fixed namespace
below): same Firebase uid always maps to the same Postgres user_id, with no
separate lookup table needed. That derived id is what gets upserted into
auth.users and returned as "uid" from every authenticated endpoint,
including /health — so /health's response is directly checkable against
`SELECT id FROM auth.users`.
"""

import uuid

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from sqlalchemy import text

from core.config import settings
from db.session import get_engine

_firebase_app: firebase_admin.App | None = None
_bearer_scheme = HTTPBearer(auto_error=False)

# Fixed namespace so the uid -> user_id derivation is stable across
# processes and deploys. Do not change this value once real users exist.
_USER_ID_NAMESPACE = uuid.UUID("6f0a2c9e-3b7a-4c8b-9c2b-2a6a8f2b7a10")


def get_firebase_app() -> firebase_admin.App:
    """Lazily initialize the Firebase Admin app (once per process).

    Uses Application Default Credentials — on Cloud Run this is the
    service account attached to the service, so no key file is needed.
    """
    global _firebase_app
    if _firebase_app is None:
        options = {"projectId": settings.FIREBASE_PROJECT_ID} if settings.FIREBASE_PROJECT_ID else {}
        _firebase_app = firebase_admin.initialize_app(credentials.ApplicationDefault(), options)
    return _firebase_app


def firebase_uid_to_user_id(firebase_uid: str) -> uuid.UUID:
    """Deterministically map a Firebase uid to the uuid used as user_id everywhere downstream."""
    return uuid.uuid5(_USER_ID_NAMESPACE, firebase_uid)


def upsert_auth_user(user_id: uuid.UUID, email: str | None) -> None:
    """Keeps FKs on ai_message_feedback/survey_response/mcp_api_keys valid
    without touching those tables — the shim is the only thing that changes.
    """
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO auth.users (id, email) VALUES (:id, :email) "
                "ON CONFLICT (id) DO UPDATE SET email = excluded.email"
            ),
            {"id": str(user_id), "email": email},
        )


async def get_current_uid(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: verify the bearer token, upsert the auth.users
    shim, and return the Postgres user_id (uuid, as a string)."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    app = get_firebase_app()
    try:
        decoded_token = firebase_auth.verify_id_token(credentials.credentials, app=app)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase ID token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = firebase_uid_to_user_id(decoded_token["uid"])
    upsert_auth_user(user_id, decoded_token.get("email"))
    return str(user_id)
