from fastapi import APIRouter, Depends

from core.auth import get_current_uid

router = APIRouter()


@router.get("/health")
async def health(uid: str = Depends(get_current_uid)):
    """Requires a valid Firebase ID token. Echoes the derived Postgres
    user_id (uuid) so the full chain (frontend -> Firebase -> Cloud Run ->
    firebase-admin -> Cloud SQL auth.users upsert) can be verified end to
    end, including that the row landed in auth.users.
    """
    return {"status": "ok", "uid": uid}
