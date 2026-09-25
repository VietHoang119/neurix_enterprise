"""Minimal async Slack Web API client (bot token), covering only the calls
this backend makes."""

import logging
from typing import Any

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

_API = "https://slack.com/api/"


class SlackApiError(RuntimeError):
    pass


class SlackClient:
    def __init__(self, token: str | None = None):
        self.token = token or settings.SLACK_BOT_TOKEN

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def call(self, method: str, payload: dict[str, Any], *, form: bool = False) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=15) as client:
            if form:
                response = await client.post(_API + method, headers=self._headers(), data=payload)
            else:
                response = await client.post(_API + method, headers=self._headers(), json=payload)
        try:
            data = response.json()
        except ValueError as exc:
            raise SlackApiError(
                f"{method} returned non-JSON (HTTP {response.status_code}): {response.text[:200]!r}"
            ) from exc
        if not data.get("ok"):
            # Slack puts the real reason in extra fields (e.g. response_metadata.messages).
            raise SlackApiError(
                f"{method} failed: {data.get('error')} | response_metadata={data.get('response_metadata')}"
            )
        return data

    async def open_dm(self, slack_user_id: str) -> str:
        data = await self.call("conversations.open", {"users": slack_user_id})
        return data["channel"]["id"]

    async def post_message(self, channel: str, text: str, blocks: list | None = None, thread_ts: str | None = None) -> str:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if blocks is not None:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        data = await self.call("chat.postMessage", payload)
        return data["ts"]

    async def update_message(self, channel: str, ts: str, text: str, blocks: list | None = None) -> None:
        # blocks=[] clears a previous card's buttons; omitting would keep them.
        await self.call("chat.update", {"channel": channel, "ts": ts, "text": text, "blocks": blocks or []})

    async def post_ephemeral(self, channel: str, user: str, text: str, thread_ts: str | None = None) -> None:
        payload: dict[str, Any] = {"channel": channel, "user": user, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        await self.call("chat.postEphemeral", payload)

    async def user_email(self, slack_user_id: str) -> str | None:
        data = await self.call("users.info", {"user": slack_user_id}, form=True)
        return (data.get("user") or {}).get("profile", {}).get("email")

    async def user_name(self, slack_user_id: str) -> str | None:
        data = await self.call("users.info", {"user": slack_user_id}, form=True)
        user = data.get("user") or {}
        profile = user.get("profile") or {}
        return profile.get("display_name") or profile.get("real_name") or user.get("name")

    async def lookup_user_by_email(self, email: str) -> str | None:
        """Slack user id for an email (users:read.email), or None."""
        try:
            data = await self.call("users.lookupByEmail", {"email": email}, form=True)
        except SlackApiError as exc:
            if "users_not_found" in str(exc):
                return None
            raise
        return (data.get("user") or {}).get("id")

    async def open_view(self, trigger_id: str, view: dict[str, Any]) -> None:
        await self.call("views.open", {"trigger_id": trigger_id, "view": view})

    async def update_view(self, view_id: str, view: dict[str, Any], view_hash: str | None = None) -> None:
        """Replace an open modal. `view_hash` makes Slack reject a stale update."""
        payload: dict[str, Any] = {"view_id": view_id, "view": view}
        if view_hash:
            payload["hash"] = view_hash
        await self.call("views.update", payload)

    async def file_info(self, file_id: str) -> dict[str, Any]:
        data = await self.call("files.info", {"file": file_id}, form=True)
        return data["file"]

    async def download_file(self, url_private: str) -> bytes:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url_private, headers=self._headers())
            response.raise_for_status()
            return response.content

    @staticmethod
    async def respond(response_url: str, payload: dict[str, Any]) -> None:
        """Reply through a command/interaction response_url."""
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(response_url, json=payload)
        if response.status_code >= 400:
            logger.warning("SLACK_RESPOND_FAILED | status=%s | body=%s", response.status_code, response.text[:200])
