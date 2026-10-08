"""ALLOWED_EMAIL_DOMAINS: unset means no restriction (the default); when
set, it only gates a brand-new auth.users row being created — an
already-existing user is never rejected by it."""

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from core import auth as auth_module
from core.auth import firebase_uid_to_user_id, get_current_uid
from core.config import settings


def _creds() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")


def _mock_decode(monkeypatch, *, uid: str, email: str | None):
    monkeypatch.setattr(
        auth_module.firebase_auth, "verify_id_token", lambda token, app=None: {"uid": uid, "email": email}
    )


def _sign_in(creds=None):
    return asyncio.run(get_current_uid(creds or _creds()))


def test_no_restriction_by_default(db, monkeypatch):
    _mock_decode(monkeypatch, uid="fb-1", email="anyone@anywhere.example")
    assert _sign_in() == str(firebase_uid_to_user_id("fb-1"))


def test_new_user_with_disallowed_domain_is_rejected(db, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_EMAIL_DOMAINS", "acme.com")
    _mock_decode(monkeypatch, uid="fb-2", email="someone@evil.example")

    with pytest.raises(HTTPException) as exc_info:
        _sign_in()
    assert exc_info.value.status_code == 403


def test_new_user_with_allowed_domain_is_accepted(db, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_EMAIL_DOMAINS", "acme.com,acme.io")
    _mock_decode(monkeypatch, uid="fb-3", email="someone@ACME.COM")  # domain match is case-insensitive

    assert _sign_in() == str(firebase_uid_to_user_id("fb-3"))


def test_new_user_with_no_email_is_rejected_when_restricted(db, monkeypatch):
    monkeypatch.setattr(settings, "ALLOWED_EMAIL_DOMAINS", "acme.com")
    _mock_decode(monkeypatch, uid="fb-4", email=None)

    with pytest.raises(HTTPException) as exc_info:
        _sign_in()
    assert exc_info.value.status_code == 403


def test_existing_user_is_never_rejected_even_if_domain_becomes_disallowed(db, monkeypatch):
    # Signs in once with no restriction in place — creates the auth.users row.
    _mock_decode(monkeypatch, uid="fb-5", email="someone@evil.example")
    first = _sign_in()

    # A restriction is added afterward, excluding this user's domain.
    monkeypatch.setattr(settings, "ALLOWED_EMAIL_DOMAINS", "acme.com")
    second = _sign_in()

    assert second == first
