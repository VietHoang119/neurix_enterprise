"""Slack endpoints: slash commands, interactivity, and the Events API.

Every request's signature is verified against SLACK_SIGNING_SECRET before
its body is parsed; anything unsigned or stale gets a 401. Handlers only
acknowledge — real work goes to background tasks so Slack's 3-second limit
is always met.
"""

import functools
import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from core.config import settings
from services.slack import handlers
from services.slack import share_blocks as sb
from services.slack import team_handlers
from services.slack.api import oauth_access
from services.slack.signature import is_valid_slack_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack")

HELP_TEXT = (
    "*Neurix commands*\n"
    "• `/neurix save <text>` — save a note (you'll get a preview to confirm)\n"
    "• `/neurix ask <question>` — ask a question answered from your notes (and your team's)\n"
    "• Send a file (PDF, image, .docx, .xlsx, .txt, .md) to me in a DM to save it\n"
    "• `/neurix team create <name>` · `/neurix team invite` · `/neurix team list`\n"
    "• `/neurix share` — share a note with a person (they accept or decline)\n"
    "• `/neurix share-team` — add a note to your team space\n"
    "• `/neurix stats` — what's new, plus insights for you and your team\n"
    "• `/neurix raw-access` — pick a shared note and let a person or your team open its original file (or stop)\n"
    "• `/neurix file` — pick a note and get a download link for its original file"
)


def _logged(task):
    """Background tasks run after Slack has been acked, so an exception has
    nowhere to go but the log. Log it with its full traceback here, once, for
    every Slack flow — a failure must never be silent."""

    @functools.wraps(task)
    async def run(**kwargs):
        try:
            await task(**kwargs)
        except Exception:
            logger.exception("SLACK_TASK_FAILED | task=%s | args=%s", task.__name__, kwargs)

    return run


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
    response = _command(form, background_tasks)
    # Queued last: BackgroundTasks run in order, so a `team create` has made
    # the team by the time this looks for it.
    if form.get("team_id") and form.get("user_id"):
        background_tasks.add_task(
            _logged(team_handlers.link_workspace),
            slack_user=form["user_id"],
            team_id=form["team_id"],
            team_domain=form.get("team_domain") or None,
        )
    return response


def _command(form: dict[str, str], background_tasks: BackgroundTasks) -> dict:
    subcommand, _, rest = (form.get("text") or "").strip().partition(" ")
    subcommand, rest = subcommand.lower(), rest.strip()
    slack_user = form.get("user_id", "")
    response_url = form.get("response_url", "")

    if subcommand == "save" and rest:
        background_tasks.add_task(
            _logged(handlers.handle_save),
            slack_user=slack_user,
            channel_id=form.get("channel_id", ""),
            response_url=response_url,
            text=rest,
        )
        return {"response_type": "ephemeral", "text": "⏳ Extracting..."}

    if subcommand == "ask" and rest:
        background_tasks.add_task(_logged(handlers.handle_ask), slack_user=slack_user, response_url=response_url, question=rest)
        return {"response_type": "ephemeral", "text": "🔎 Searching your notes..."}

    trigger_id = form.get("trigger_id", "")
    user_args = {"slack_user": slack_user, "response_url": response_url}

    if subcommand == "team":
        action, _, name = rest.partition(" ")
        action = action.lower()
        if action == "create":
            background_tasks.add_task(_logged(team_handlers.handle_team_create), name=name, **user_args)
            return {"response_type": "ephemeral", "text": "⏳ Creating your team..."}
        if action == "invite":
            background_tasks.add_task(_logged(team_handlers.open_team_invite), trigger_id=trigger_id, **user_args)
            return {"response_type": "ephemeral", "text": "⏳ Opening..."}
        if action in ("", "list"):
            background_tasks.add_task(_logged(team_handlers.handle_team_list), **user_args)
            return {"response_type": "ephemeral", "text": "⏳ Loading your team..."}

    if subcommand in ("share", "share-team"):
        background_tasks.add_task(
            _logged(team_handlers.open_share), trigger_id=trigger_id, team=subcommand == "share-team", **user_args
        )
        return {"response_type": "ephemeral", "text": "⏳ Opening..."}

    if subcommand == "stats":
        background_tasks.add_task(_logged(team_handlers.handle_stats), **user_args)
        return {"response_type": "ephemeral", "text": "📊 Crunching your stats..."}

    if subcommand == "raw-access":
        background_tasks.add_task(_logged(team_handlers.open_raw_access), trigger_id=trigger_id, **user_args)
        return {"response_type": "ephemeral", "text": "⏳ Opening..."}

    if subcommand == "file":
        background_tasks.add_task(_logged(team_handlers.open_file), trigger_id=trigger_id, **user_args)
        return {"response_type": "ephemeral", "text": "⏳ Opening..."}

    return {"response_type": "ephemeral", "text": HELP_TEXT}


@router.get("/oauth/callback")
async def oauth_callback(code: str | None = None, error: str | None = None):
    """Slack's redirect after someone installs the app ("Add to Slack").
    Exchanges the code and links the installer's team to that workspace,
    then sends them to the web app. The bot token in the response is not
    stored: the backend still runs on the one SLACK_BOT_TOKEN."""
    if not (settings.SLACK_CLIENT_ID and settings.SLACK_CLIENT_SECRET):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    outcome = "cancelled"
    if code and not error:
        try:
            data = await oauth_access(
                client_id=settings.SLACK_CLIENT_ID,
                client_secret=settings.SLACK_CLIENT_SECRET,
                code=code,
                redirect_uri=settings.SLACK_OAUTH_REDIRECT_URI,
            )
            linked = await team_handlers.link_installer_workspace(
                bot_token=data.get("access_token") or "",
                installer_slack=(data.get("authed_user") or {}).get("id") or "",
                team_id=(data.get("team") or {}).get("id") or "",
            )
            outcome = "linked" if linked else "installed"
        except Exception:  # Slack, network, or DB: the person still lands on the web app
            logger.exception("SLACK_OAUTH_FAILED")
            outcome = "error"
    return RedirectResponse(f"{settings.FRONTEND_URL}/?slack_install={outcome}", status_code=status.HTTP_302_FOUND)


@router.post("/interactions")
async def interactions(request: Request, background_tasks: BackgroundTasks):
    form = _form(await _verified_body(request))
    payload = json.loads(form.get("payload") or "{}")
    slack_user = (payload.get("user") or {}).get("id", "")

    if payload.get("type") == "view_submission":
        # {} closes the modal; the outcome is DM'd to the submitter.
        return _submit_view(payload.get("view") or {}, slack_user, background_tasks) or {}

    if payload.get("type") != "block_actions" or not payload.get("actions"):
        return {}

    action = payload["actions"][0]
    action_id = action.get("action_id", "")
    coords = {
        "slack_user": slack_user,
        "channel": (payload.get("container") or {}).get("channel_id") or (payload.get("channel") or {}).get("id", ""),
        "message_ts": (payload.get("container") or {}).get("message_ts") or (payload.get("message") or {}).get("ts", ""),
    }
    if action_id in sb.SHARE_ACTIONS:
        background_tasks.add_task(
            _logged(team_handlers.handle_share_action), action_id=action_id, share_id=action.get("value", ""), **coords
        )
    elif action_id in (handlers.CONFIRM_ACTION, handlers.EDIT_ACTION, handlers.DISCARD_ACTION):
        background_tasks.add_task(_logged(handlers.handle_action), action_id=action_id, draft_id=action.get("value", ""), **coords)
    elif action_id == sb.NODE_BLOCK and (payload.get("view") or {}).get("callback_id") == sb.RAW_ACCESS_MODAL:
        view = payload["view"]
        background_tasks.add_task(
            _logged(team_handlers.update_raw_access_view),
            slack_user=slack_user,
            view_id=view.get("id", ""),
            view_hash=view.get("hash"),
            node_id=(action.get("selected_option") or {}).get("value", ""),
        )
    # Anything else (e.g. picking an option inside a modal) needs no work.
    return {}


def _submit_view(view: dict, slack_user: str, background_tasks: BackgroundTasks) -> dict | None:
    """Queue a modal's submission; a returned dict is sent back to Slack
    instead of closing the modal (e.g. field errors)."""
    values = (view.get("state") or {}).get("values") or {}

    def field(block: str) -> dict:
        return (values.get(block) or {}).get(block) or {}

    def option(block: str) -> str:
        return (field(block).get("selected_option") or {}).get("value", "")

    callback_id = view.get("callback_id")
    if callback_id == sb.SHARE_MODAL:
        background_tasks.add_task(
            _logged(team_handlers.submit_share),
            slack_user=slack_user,
            node_id=option(sb.NODE_BLOCK),
            recipient_slack=field(sb.RECIPIENT_BLOCK).get("selected_user") or "",
            message=(field(sb.MESSAGE_BLOCK).get("value") or "").strip() or None,
        )
    elif callback_id == sb.SHARE_TEAM_MODAL:
        background_tasks.add_task(
            _logged(team_handlers.submit_share_team),
            slack_user=slack_user,
            node_id=option(sb.NODE_BLOCK),
            scope=option(sb.SCOPE_BLOCK) or "admin",
            targets_slack=field(sb.TARGETS_BLOCK).get("selected_users") or [],
        )
    elif callback_id == sb.TEAM_INVITE_MODAL:
        background_tasks.add_task(
            _logged(team_handlers.submit_team_invite),
            slack_user=slack_user,
            member_slack=field(sb.MEMBER_BLOCK).get("selected_user") or "",
        )
    elif callback_id == sb.RAW_ACCESS_MODAL:
        node_id = option(sb.NODE_BLOCK)
        target_block = values.get(f"{sb.TARGET_BLOCK}.{node_id}") or {}
        target = ((target_block.get(sb.TARGET_BLOCK) or {}).get("selected_option") or {}).get("value", "")
        if not target:  # submitted before the note's shares were shown
            return {"response_action": "errors", "errors": {sb.NODE_BLOCK: "Pick a note, then who it's shared with."}}
        background_tasks.add_task(
            _logged(team_handlers.submit_raw_access),
            slack_user=slack_user,
            node_id=node_id,
            target=target,
            on=option(sb.ACCESS_BLOCK) == "on",
        )
    elif callback_id == sb.FILE_MODAL:
        background_tasks.add_task(_logged(team_handlers.submit_file), slack_user=slack_user, node_id=option(sb.NODE_BLOCK))
    return None


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

    # TEMP (edit-flow diagnosis): every DM message event, and whether it is
    # routed to the edit-reply handler. Remove once the Edit flow is verified.
    if etype == "message" and event.get("channel_type") == "im":
        logger.info(
            "SLACK_DM_MESSAGE | channel=%s | user=%s | ts=%s | thread_ts=%s | subtype=%s | bot_id=%s",
            event.get("channel"), event.get("user"), event.get("ts"), event.get("thread_ts"),
            event.get("subtype"), event.get("bot_id"),
        )

    if (
        etype == "message"
        and event.get("channel_type") == "im"
        and not event.get("subtype")
        and not event.get("bot_id")
        and event.get("thread_ts")
        and event.get("thread_ts") != event.get("ts")
    ):
        background_tasks.add_task(
            _logged(handlers.handle_thread_reply),
            channel=event.get("channel", ""),
            thread_ts=event["thread_ts"],
            slack_user=event.get("user", ""),
            text=event.get("text") or "",
        )
    elif etype == "message" and event.get("channel_type") == "im" and not event.get("bot_id"):
        # TEMP (edit-flow diagnosis): a user DM message that is NOT treated as an edit reply.
        reason = (
            f"subtype={event.get('subtype')}" if event.get("subtype")
            else "not_in_thread" if not event.get("thread_ts") or event.get("thread_ts") == event.get("ts")
            else "unknown"
        )
        logger.info("EDIT_REPLY_SKIPPED | channel=%s | ts=%s | reason=%s", event.get("channel"), event.get("ts"), reason)
    elif etype == "file_shared" and (event.get("channel_id") or "").startswith("D"):
        # DMs with the bot only — files shared in channels are never ingested.
        background_tasks.add_task(
            _logged(handlers.handle_file_shared),
            file_id=event.get("file_id") or (event.get("file") or {}).get("id", ""),
            slack_user=event.get("user_id", ""),
            channel_id=event["channel_id"],
        )

    return {"ok": True}
