from fastapi import APIRouter, Depends

from core.auth import get_current_uid

router = APIRouter()


@router.get("/health")
async def health(uid: str = Depends(get_current_uid)):
    """Requires a valid Firebase ID token. Echoes the decoded uid so the full
    chain (frontend -> Firebase -> Cloud Run -> firebase-admin) can be
    verified end to end.
    """
    return {"status": "ok", "uid": uid}
