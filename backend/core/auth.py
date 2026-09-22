"""Firebase ID token verification.

Replaces neurix's SupabaseAuth/SupabaseAuthMiddleware (app/middleware/auth_middleware.py).
The decoded Firebase `uid` becomes the new `user_id` used everywhere downstream — this
is a clean cutover, so no attempt is made to map Firebase uids back to old Supabase UUIDs.
"""

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from core.config import settings

_firebase_app: firebase_admin.App | None = None
_bearer_scheme = HTTPBearer(auto_error=False)


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


async def get_current_uid(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: verify the bearer token and return the Firebase uid."""
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

    return decoded_token["uid"]
