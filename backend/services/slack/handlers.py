"""Slack flows. Everything here runs as a FastAPI background task, after the
HTTP handler has already acknowledged Slack within its 3-second limit.

The preview card is always posted as a real bot message in the user's DM
with the app (not as an ephemeral response_url reply): ephemeral messages
cannot be edited with chat.update and cannot have thread replies, and both
are needed (Confirm/final status edit the card in place; Edit listens for a
thread reply). It also keeps personal notes out of shared channels.
"""

import asyncio
import logging
import re

from core.config import settings
from db.session import get_engine
from repositories import drafts as drafts_repo
from repositories import orgs as orgs_repo
from repositories import users as users_repo
from services import ask as ask_service
from services import ingest
from services import raw_storage
from services.file_extract import UnsupportedFileError, extract_text, file_kind
from services.slack.api import SlackClient
from services.slack.blocks import (
    CONFIRM_ACTION,
    DISCARD_ACTION,
    EDIT_ACTION,
    escape,
    preview_blocks,
    status_blocks,
)
from services.slack.edit_parser import apply_edit

logger = logging.getLogger(__name__)

EDIT_INSTRUCTIONS = (
    "Reply in this thread with new summary text, or `+key: X` / `-key: Y` to add/remove a key."
)
NOT_LINKED = (
    "I couldn't match your Slack account to a Neurix account. Sign in to the Neurix web app once "
    "with the same email as your Slack profile, then try again."
)

# Slack user id -> Postgres user_id. Stable mapping, so a process-lifetime
# cache is safe; misses are not cached (the user may sign in later).
_user_cache: dict[str, str] = {}


def slack_client() -> SlackClient:
    return SlackClient()


async def resolve_user_id(client: SlackClient, slack_user_id: str) -> str | None:
    if slack_user_id in _user_cache:
        return _user_cache[slack_user_id]
    email = await client.user_email(slack_user_id)
    if not email:
        return None

    def _lookup():
        with get_engine().connect() as conn:
            return users_repo.find_user_id_by_email(conn, email)

    user_id = await asyncio.to_thread(_lookup)
    if user_id:
        _user_cache[slack_user_id] = user_id
    return user_id


def _to_slack_mrkdwn(text: str) -> str:
    # Gemini answers in Markdown; Slack bold is *x*, not **x**.
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)


# --------------------------------------------------------------------------
# Ingest -> preview card
# --------------------------------------------------------------------------

async def _post_preview(
    client: SlackClient,
    *,
    user_id: str,
    slack_user: str,
    raw_content: str,
    source: str,
    dm_channel: str,
    placeholder_ts: str | None = None,
    original: tuple[bytes, str | None, str] | None = None,
) -> None:
    """Extract, stage a node_drafts row, and show the preview card (posting
    a new message, or turning `placeholder_ts` into the card).

    `original` is (bytes, filename, content_type) of an uploaded file; a text
    note's original is its text. Either is kept in Cloud Storage and its
    path carried into node_pool.metadata.raw_storage_path on Confirm."""
    payload = await ingest.build_preview(user_id=user_id, raw_content=raw_content, source=source)
    data, filename, content_type = original or (raw_content.encode("utf-8"), None, "text/plain; charset=utf-8")
    raw_path = await asyncio.to_thread(
        raw_storage.store_original, user_id=user_id, data=data, filename=filename, content_type=content_type
    )
    if raw_path:
        payload["node_payload"].setdefault("metadata", {})["raw_storage_path"] = raw_path

    def _create():
        with get_engine().begin() as conn:
            return drafts_repo.create_draft(conn, user_id=user_id, raw_content=raw_content, source=source, preview=payload)

    draft = await asyncio.to_thread(_create)
    blocks = preview_blocks(draft["id"], payload["preview"])
    fallback = f"Preview: {payload['preview']['title']}"
    if placeholder_ts:
        await client.update_message(dm_channel, placeholder_ts, fallback, blocks)
        ts = placeholder_ts
    else:
        ts = await client.post_message(dm_channel, fallback, blocks)

    payload["slack"] = {"user": slack_user, "channel": dm_channel, "ts": ts}

    def _save_coords():
        with get_engine().begin() as conn:
            drafts_repo.update_preview(conn, draft["id"], payload)

    await asyncio.to_thread(_save_coords)


async def handle_save(*, slack_user: str, channel_id: str, response_url: str, text: str) -> None:
    client = slack_client()
    try:
        user_id = await resolve_user_id(client, slack_user)
        if not user_id:
            await client.respond(response_url, {"replace_original": True, "text": NOT_LINKED})
            return
        dm_channel = await client.open_dm(slack_user)
        await _post_preview(
            client, user_id=user_id, slack_user=slack_user, raw_content=text, source="slack", dm_channel=dm_channel
        )
        if channel_id == dm_channel:
            await client.respond(response_url, {"delete_original": True})
        else:
            await client.respond(
                response_url, {"replace_original": True, "text": "📬 Preview ready — check your DM with Neurix."}
            )
    except Exception as exc:
        logger.error("SLACK_SAVE_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await client.respond(response_url, {"replace_original": True, "text": "⚠️ Sorry, extraction failed. Please try again."})


async def handle_file_shared(*, file_id: str, slack_user: str, channel_id: str) -> None:
    client = slack_client()
    placeholder_ts: str | None = None
    try:
        info = await client.file_info(file_id)
        name = info.get("name") or "file"
        placeholder_ts = await client.post_message(channel_id, f"⏳ Extracting {name}...")

        user_id = await resolve_user_id(client, slack_user)
        if not user_id:
            await client.update_message(channel_id, placeholder_ts, NOT_LINKED)
            return
        file_kind(info)  # raises UnsupportedFileError before downloading
        if (info.get("size") or 0) > settings.SLACK_MAX_FILE_BYTES:
            raise UnsupportedFileError(
                f"{name} is too large (limit {settings.SLACK_MAX_FILE_BYTES // (1024 * 1024)} MB)."
            )
        data = await client.download_file(info.get("url_private_download") or info["url_private"])
        text = await extract_text(info, data)
        await _post_preview(
            client,
            user_id=user_id,
            slack_user=slack_user,
            raw_content=text,
            source=name,
            dm_channel=channel_id,
            placeholder_ts=placeholder_ts,
            original=(data, name, info.get("mimetype") or "application/octet-stream"),
        )
    except UnsupportedFileError as exc:
        await _report(client, channel_id, placeholder_ts, f"⚠️ {exc}")
    except Exception as exc:
        logger.error("SLACK_FILE_FAILED | file_id=%s | error=%s", file_id, exc, exc_info=True)
        await _report(client, channel_id, placeholder_ts, "⚠️ Sorry, I couldn't process that file. Please try again.")


async def _report(client: SlackClient, channel: str, ts: str | None, text: str) -> None:
    try:
        if ts:
            await client.update_message(channel, ts, text)
        else:
            await client.post_message(channel, text)
    except Exception:
        logger.exception("SLACK_REPORT_FAILED")


# --------------------------------------------------------------------------
# Preview card buttons
# --------------------------------------------------------------------------

async def handle_action(*, action_id: str, draft_id: str, slack_user: str, channel: str, message_ts: str) -> None:
    client = slack_client()

    def _get():
        with get_engine().connect() as conn:
            return drafts_repo.get_draft(conn, draft_id)

    draft = await asyncio.to_thread(_get)
    if draft is None:
        await client.post_ephemeral(channel, slack_user, "This preview was already confirmed or discarded.")
        return
    if (draft["preview"].get("slack") or {}).get("user") != slack_user:
        await client.post_ephemeral(channel, slack_user, "Only the person who created this preview can change it.")
        return

    if action_id == EDIT_ACTION:
        await client.post_ephemeral(channel, slack_user, EDIT_INSTRUCTIONS, thread_ts=message_ts)
        return
    if action_id not in (CONFIRM_ACTION, DISCARD_ACTION):
        return

    def _claim():
        with get_engine().begin() as conn:
            return drafts_repo.claim_draft(conn, draft_id)

    claimed = await asyncio.to_thread(_claim)
    if claimed is None:  # lost a race with another click
        return
    title = claimed["preview"]["preview"].get("title") or "Untitled"

    if action_id == DISCARD_ACTION:
        raw_path = (claimed["preview"].get("node_payload") or {}).get("metadata", {}).get("raw_storage_path")
        await asyncio.to_thread(raw_storage.discard_original, raw_path)
        await client.update_message(channel, message_ts, "Discarded.", status_blocks(title, "Discarded."))
        return

    try:
        confirmed = await asyncio.to_thread(ingest.confirm_draft, claimed)
    except Exception as exc:
        logger.error("SLACK_CONFIRM_FAILED | draft_id=%s | error=%s", draft_id, exc, exc_info=True)
        failed = "⚠️ Couldn't save this note. Please send it again."
        await client.update_message(channel, message_ts, failed, status_blocks(title, failed))
        return
    processing = "⏳ Saved — processing connections..."
    await client.update_message(channel, message_ts, processing, status_blocks(title, processing))
    try:
        result = await ingest.process_node(confirmed)
        final = ingest.done_message(result)
    except Exception:
        final = "⚠️ Saved, but processing failed. The note is marked as failed."
    await client.update_message(channel, message_ts, final, status_blocks(title, escape(final)))


# --------------------------------------------------------------------------
# Edit replies (thread replies to a preview card in the DM)
# --------------------------------------------------------------------------

async def handle_thread_reply(*, channel: str, thread_ts: str, slack_user: str, text: str) -> None:
    def _find():
        with get_engine().connect() as conn:
            return drafts_repo.find_draft_by_slack_message(conn, channel=channel, ts=thread_ts)

    draft = await asyncio.to_thread(_find)
    if draft is None or (draft["preview"].get("slack") or {}).get("user") != slack_user:
        return  # not a reply to a pending preview of this user

    client = slack_client()
    payload = draft["preview"]
    changes = apply_edit(payload, text)
    if not changes:
        await client.post_message(channel, "No changes detected. " + EDIT_INSTRUCTIONS, thread_ts=thread_ts)
        return

    def _save():
        with get_engine().begin() as conn:
            drafts_repo.update_preview(conn, draft["id"], payload)

    await asyncio.to_thread(_save)
    await client.update_message(
        channel, thread_ts, f"Preview: {payload['preview']['title']}", preview_blocks(draft["id"], payload["preview"])
    )
    await client.post_message(channel, "✏️ Preview updated: " + ", ".join(changes) + ".", thread_ts=thread_ts)


# --------------------------------------------------------------------------
# /neurix ask
# --------------------------------------------------------------------------

async def handle_ask(*, slack_user: str, response_url: str, question: str) -> None:
    client = slack_client()
    try:
        user_id = await resolve_user_id(client, slack_user)
        if not user_id:
            await client.respond(response_url, {"replace_original": True, "text": NOT_LINKED})
            return
        def _org():
            with get_engine().connect() as conn:
                membership = orgs_repo.get_membership(conn, user_id)
            return membership["org_id"] if membership else None

        # Org scope whenever the user is in a team: their own notes, accepted
        # shares, and everything in the team space.
        result = await ask_service.answer_question(user_id, question, org_id=await asyncio.to_thread(_org))
        text = f"*Q:* {escape(question)}\n\n{_to_slack_mrkdwn(result.answer)}"
        if result.sources:
            text += "\n\n_Sources:_ " + " · ".join(f"[{s['n']}] {escape(s['title'])}" for s in result.sources)
        await client.respond(response_url, {"replace_original": True, "text": text})
    except Exception as exc:
        logger.error("SLACK_ASK_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await client.respond(response_url, {"replace_original": True, "text": "⚠️ Sorry, I couldn't answer that. Please try again."})

