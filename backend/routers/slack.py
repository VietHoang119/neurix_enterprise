"""Slack endpoints: slash commands, interactivity, and the Events API.

Every request's signature is verified against SLACK_SIGNING_SECRET before
its body is parsed; anything unsigned or stale gets a 401. Handlers only
acknowledge — real work goes to background tasks so Slack's 3-second limit
is always met.
"""

import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from core.config import settings
from services.slack import handlers
from services.slack.signature import is_valid_slack_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack")

HELP_TEXT = (
    "*Neurix commands*\n"
    "• `/neurix save <text>` — save a note (you'll get a preview to confirm)\n"
    "• `/neurix ask <question>` — ask a question answered from your notes\n"
    "• Send a file (PDF, image, .docx, .xlsx, .txt, .md) to me in a DM to save it"
)


async def _verified_body(request: Request) -> bytes:
    body = await request.body()
    if not is_valid_slack_signature(
        signing_secret=settings.SLACK_SIGNING_SECRET,
        body=body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        signature=request.headers.get("X-Slack-Signature"),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Slack signature")
    return body


def _form(body: bytes) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}


@router.post("/commands")
async def commands(request: Request, background_tasks: BackgroundTasks):
    form = _form(await _verified_body(request))
    subcommand, _, rest = (form.get("text") or "").strip().partition(" ")
    subcommand, rest = subcommand.lower(), rest.strip()
    slack_user = form.get("user_id", "")
    response_url = form.get("response_url", "")

    if subcommand == "save" and rest:
        background_tasks.add_task(
            handlers.handle_save,
            slack_user=slack_user,
            channel_id=form.get("channel_id", ""),
            response_url=response_url,
            text=rest,
        )
        return {"response_type": "ephemeral", "text": "⏳ Extracting..."}

    if subcommand == "ask" and rest:
        background_tasks.add_task(handlers.handle_ask, slack_user=slack_user, response_url=response_url, question=rest)
        return {"response_type": "ephemeral", "text": "🔎 Searching your notes..."}

    return {"response_type": "ephemeral", "text": HELP_TEXT}


@router.post("/interactions")
async def interactions(request: Request, background_tasks: BackgroundTasks):
    form = _form(await _verified_body(request))
    payload = json.loads(form.get("payload") or "{}")
    if payload.get("type") != "block_actions" or not payload.get("actions"):
        return {}

    action = payload["actions"][0]
    background_tasks.add_task(
        handlers.handle_action,
        action_id=action.get("action_id", ""),
        draft_id=action.get("value", ""),
        slack_user=(payload.get("user") or {}).get("id", ""),
        channel=(payload.get("container") or {}).get("channel_id") or (payload.get("channel") or {}).get("id", ""),
        message_ts=(payload.get("container") or {}).get("message_ts") or (payload.get("message") or {}).get("ts", ""),
    )
    return {}


@router.post("/events")
async def events(request: Request, background_tasks: BackgroundTasks):
    data = json.loads(await _verified_body(request) or b"{}")

    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # Slack re-sends an event it thinks we missed; we always ack at once and
    # work in the background, so a retry is a duplicate — never re-process.
    if request.headers.get("X-Slack-Retry-Num"):
        return {"ok": True}

    event = data.get("event") or {}
    etype = event.get("type")

    if (
        etype == "message"
        and event.get("channel_type") == "im"
        and not event.get("subtype")
        and not event.get("bot_id")
        and event.get("thread_ts")
        and event.get("thread_ts") != event.get("ts")
    ):
        background_tasks.add_task(
            handlers.handle_thread_reply,
            channel=event.get("channel", ""),
            thread_ts=event["thread_ts"],
            slack_user=event.get("user", ""),
            text=event.get("text") or "",
        )
    elif etype == "file_shared" and (event.get("channel_id") or "").startswith("D"):
        # DMs with the bot only — files shared in channels are never ingested.
        background_tasks.add_task(
            handlers.handle_file_shared,
            file_id=event.get("file_id") or (event.get("file") or {}).get("id", ""),
            slack_user=event.get("user_id", ""),
            channel_id=event["channel_id"],
        )

    return {"ok": True}
