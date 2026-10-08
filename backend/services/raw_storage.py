"""Original-content storage on Cloud Storage, so `view_raw` grants access to
a real file. The object path goes into node_pool.metadata.raw_storage_path
(no new column).

Signed URLs: on Cloud Run the default credentials hold no private key, so
signing goes through the IAM signBlob API — the runtime service account
needs roles/iam.serviceAccountTokenCreator on itself (see the runbook).
Locally, a service-account key file signs directly.
"""

import logging
import re
import time
import uuid
from datetime import timedelta
from functools import cache

from core.config import settings

logger = logging.getLogger(__name__)

TEXT_NOTE_NAME = "note.txt"


def object_path(user_id: str, filename: str | None, now: float | None = None) -> str:
    """{TENANT_ID}/raw/{uid}/{timestamp}-{filename_or_note} (just
    raw/{uid}/... when TENANT_ID is unset — a single-tenant/local setup).
    The timestamp carries a short random suffix: two notes saved in the
    same second must not overwrite each other's original.

    Write side only: reads always go through whatever path was already
    stored in node_pool.metadata.raw_storage_path (services/raw_files.py),
    so this only changes the shape of new writes going forward — existing
    rows keep resolving via their own stored path, unprefixed or not.
    """
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or TEXT_NOTE_NAME).strip("._") or TEXT_NOTE_NAME
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(time.time() if now is None else now)) + uuid.uuid4().hex[:6]
    prefix = f"{settings.TENANT_ID}/" if settings.TENANT_ID else ""
    return f"{prefix}raw/{user_id}/{stamp}-{name[:100]}"


class RawStorage:
    def __init__(self, bucket: str):
        from google.cloud import storage

        self._bucket = storage.Client().bucket(bucket)

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        self._bucket.blob(path).upload_from_string(data, content_type=content_type)

    def delete(self, path: str) -> None:
        self._bucket.blob(path).delete()

    def signed_url(self, path: str, ttl_seconds: int) -> str:
        import google.auth
        from google.auth.transport.requests import Request

        blob = self._bucket.blob(path)
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if hasattr(credentials, "sign_bytes"):  # a key file: sign locally
            return blob.generate_signed_url(version="v4", expiration=timedelta(seconds=ttl_seconds), method="GET")
        credentials.refresh(Request())
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=ttl_seconds),
            method="GET",
            service_account_email=credentials.service_account_email,
            access_token=credentials.token,
        )


@cache
def _storage(bucket: str) -> RawStorage:
    return RawStorage(bucket)


def get_raw_storage() -> RawStorage | None:
    """None when RAW_STORAGE_BUCKET is unset (raw files are then not kept)."""
    if not settings.RAW_STORAGE_BUCKET:
        return None
    return _storage(settings.RAW_STORAGE_BUCKET)


def store_original(*, user_id: str, data: bytes, filename: str | None, content_type: str) -> str | None:
    """Upload the original content; return its object path, or None if raw
    storage is not configured or the upload failed. Never raises: a missing
    raw copy must not block ingest."""
    storage = get_raw_storage()
    if storage is None:
        return None
    path = object_path(user_id, filename)
    try:
        storage.upload(path, data, content_type)
        return path
    except Exception as exc:
        logger.warning("RAW_UPLOAD_FAILED | user_id=%s | path=%s | error=%s", user_id, path, exc)
        return None


def discard_original(path: str | None) -> None:
    """Best-effort delete of a raw object whose draft was discarded."""
    storage = get_raw_storage()
    if storage is None or not path:
        return
    try:
        storage.delete(path)
    except Exception as exc:
        logger.warning("RAW_DELETE_FAILED | path=%s | error=%s", path, exc)
