"""Slack flows for Week 3: team membership, person/team sharing, stats,
and raw-file access. Like handlers.py, everything here runs as a FastAPI
background task after Slack has been acknowledged.

Modal submissions have no response_url, so their outcome (success or the
reason it failed) is DM'd to the submitter.
"""

import asyncio
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError

from db.session import get_engine
from repositories import nodes as nodes_repo
from repositories import notifications as notifications_repo
from repositories import orgs as orgs_repo
from repositories import shares as shares_repo
from repositories import users as users_repo
from services import insights
from services import raw_files
from services import share as share_service
from services import team as team_service
from services.share import NOTIFY_SCOPES, ShareError
from services.slack import handlers as base
from services.slack import share_blocks as sb
from services.slack.api import SlackClient
from services.slack.blocks import escape

logger = logging.getLogger(__name__)

NOT_IN_TEAM = "You're not in a team yet. Create one with `/neurix team create <name>`."

# Postgres user_id -> Slack user id (found by email). Misses are not cached.
_slack_id_cache: dict[str, str] = {}


def _db(fn, *args, **kwargs):
    """Run `fn(conn, ...)` in one transaction, in a worker thread."""

    def run():
        with get_engine().begin() as conn:
            return fn(conn, *args, **kwargs)

    return asyncio.to_thread(run)


async def _reply(response_url: str, text: str, blocks: list | None = None) -> None:
    payload: dict[str, Any] = {"replace_original": True, "text": text}
    if blocks is not None:
        payload["blocks"] = blocks
    await base.slack_client().respond(response_url, payload)


async def _dm(client: SlackClient, slack_user: str, text: str, blocks: list | None = None) -> tuple[str, str]:
    channel = await client.open_dm(slack_user)
    ts = await client.post_message(channel, text, blocks)
    return channel, ts


async def slack_user_for(client: SlackClient, user_id: str) -> str | None:
    """The Slack user behind a Neurix user_id (via their email), or None."""
    if user_id in _slack_id_cache:
        return _slack_id_cache[user_id]
    emails = await _db(users_repo.get_emails, [user_id])
    if user_id not in emails:
        return None
    slack_id = await client.lookup_user_by_email(emails[user_id])
    if slack_id:
        _slack_id_cache[user_id] = slack_id
    return slack_id


async def _name(client: SlackClient, slack_user: str) -> str:
    try:
        return await client.user_name(slack_user) or "Someone"
    except Exception:
        logger.warning("SLACK_USER_NAME_FAILED | slack_user=%s", slack_user, exc_info=True)
        return "Someone"


async def _linked_user(client: SlackClient, slack_user: str, response_url: str | None = None) -> str | None:
    """Resolve the caller; tell them how to link their account if needed."""
    user_id = await base.resolve_user_id(client, slack_user)
    if not user_id:
        if response_url:
            await _reply(response_url, base.NOT_LINKED)
        else:
            await _dm(client, slack_user, base.NOT_LINKED)
    return user_id


# --------------------------------------------------------------------------
# Slack workspace link (orgs.slack_team_id / slack_workspace_url)
# --------------------------------------------------------------------------

async def link_workspace(*, slack_user: str, team_id: str, team_domain: str | None) -> None:
    """Runs after every slash command: if the caller's team has no Slack
    workspace yet, it is this one. Covers teams created before the OAuth
    install callback existed and workspaces installed from the Slack app
    dashboard (which never reaches our callback). Silent either way."""
    user_id = await base.resolve_user_id(base.slack_client(), slack_user)
    if user_id and await _db(team_service.link_slack_workspace, user_id=user_id, team_id=team_id, domain=team_domain):
        logger.info("SLACK_WORKSPACE_LINKED | via=command | slack_user=%s | team_id=%s", slack_user, team_id)


async def link_installer_workspace(*, bot_token: str, installer_slack: str, team_id: str) -> bool:
    """OAuth install: link the installer's team to the workspace they just
    installed into (overwriting any earlier link — installing is explicit).
    False when the installer has no Neurix account or no team yet; their
    team is then linked by its first slash command instead."""
    user_id = await base.resolve_user_id(SlackClient(bot_token), installer_slack)
    if not user_id:
        return False
    linked = await _db(team_service.link_slack_workspace, user_id=user_id, team_id=team_id, overwrite=True)
    if linked:
        logger.info("SLACK_WORKSPACE_LINKED | via=install | slack_user=%s | team_id=%s", installer_slack, team_id)
    return linked


# --------------------------------------------------------------------------
# /neurix team create | invite | list
# --------------------------------------------------------------------------

async def handle_team_create(*, slack_user: str, response_url: str, name: str) -> None:
    client = base.slack_client()
    try:
        user_id = await _linked_user(client, slack_user, response_url)
        if not user_id:
            return
        name = name.strip()[:100]
        if not name:
            await _reply(response_url, "Usage: `/neurix team create <name>`")
            return
        display_name = await _name(client, slack_user)
        membership, created = await _db(team_service.create_team, user_id=user_id, name=name, display_name=display_name)
        if not created:
            await _reply(response_url, f"You're already in team *{escape(membership['name'])}* — one team per person.")
            return
        await _reply(
            response_url,
            f"✅ Created team *{escape(name)}* — you're its admin. Add people with `/neurix team invite`.",
        )
    except Exception as exc:
        logger.error("SLACK_TEAM_CREATE_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await _reply(response_url, "⚠️ Sorry, I couldn't create the team. Please try again.")


async def handle_team_list(*, slack_user: str, response_url: str) -> None:
    client = base.slack_client()
    user_id = await _linked_user(client, slack_user, response_url)
    if not user_id:
        return

    membership, members = await _db(team_service.list_team, user_id)
    if not membership:
        await _reply(response_url, NOT_IN_TEAM)
        return
    lines = [
        f"• {escape(m['display_name'] or m['email'] or 'someone')}"
        + (f" ({escape(m['email'])})" if m["email"] and m["display_name"] else "")
        + (" — admin" if m["role"] == "admin" else "")
        for m in members
    ]
    await _reply(response_url, f"*Team {escape(membership['name'])}* — {len(members)} member(s)\n" + "\n".join(lines))


async def open_team_invite(*, slack_user: str, trigger_id: str, response_url: str) -> None:
    client = base.slack_client()
    user_id = await _linked_user(client, slack_user, response_url)
    if not user_id:
        return
    membership = await _db(orgs_repo.get_membership, user_id)
    if not membership:
        await _reply(response_url, NOT_IN_TEAM)
        return
    if membership["role"] != "admin":
        await _reply(response_url, "Only a team admin can add members.")
        return
    await client.open_view(trigger_id, sb.team_invite_modal(membership["name"]))
    await _reply(response_url, "📝 Opened.")  # delete_original fails with `no_text` on slash commands


async def submit_team_invite(*, slack_user: str, member_slack: str) -> None:
    client = base.slack_client()
    user_id = await _linked_user(client, slack_user)
    if not user_id:
        return
    email = await client.user_email(member_slack)
    member_id = await _db(team_service.find_invitee, email) if email else None
    if not member_id:
        await _dm(
            client,
            slack_user,
            f"⚠️ Couldn't add <@{member_slack}>: they have never signed in to the Neurix web app"
            + (f" with {escape(email)}" if email else "")
            + ". Ask them to sign in once with their Slack email, then invite them again.",
        )
        return
    display_name = await _name(client, member_slack)
    try:
        membership, existing = await _db(
            team_service.add_member, admin_id=user_id, member_id=member_id, display_name=display_name
        )
    except team_service.TeamError:
        await _dm(client, slack_user, "Only a team admin can add members.")
        return
    if existing:
        where = "your team" if existing["org_id"] == membership["org_id"] else f"team *{escape(existing['name'])}*"
        await _dm(client, slack_user, f"<@{member_slack}> is already in {where}.")
        return
    team = escape(membership["name"])
    await _dm(client, slack_user, f"✅ Added <@{member_slack}> to *{team}*.")
    await _dm(client, member_slack, f"👋 <@{slack_user}> added you to team *{team}*. Try `/neurix share-team`.")


# --------------------------------------------------------------------------
# /neurix share (person, needs Accept) and /neurix share-team
# --------------------------------------------------------------------------

async def open_share(*, slack_user: str, trigger_id: str, response_url: str, team: bool) -> None:
    client = base.slack_client()
    user_id = await _linked_user(client, slack_user, response_url)
    if not user_id:
        return

    def read(conn):
        return orgs_repo.get_membership(conn, user_id), nodes_repo.recent_ready_nodes(conn, user_id)

    membership, nodes = await _db(read)
    if team and not membership:
        await _reply(response_url, NOT_IN_TEAM)
        return
    if not nodes:
        await _reply(response_url, "You have no ready notes to share yet. Save one with `/neurix save <text>`.")
        return
    view = sb.share_team_modal(nodes, membership["name"]) if team else sb.share_modal(nodes)
    await client.open_view(trigger_id, view)
    await _reply(response_url, "📝 Opened.")  # delete_original fails with `no_text` on slash commands


async def submit_share(*, slack_user: str, node_id: str, recipient_slack: str, message: str | None) -> None:
    client = base.slack_client()
    try:
        sender_id = await _linked_user(client, slack_user)
        if not sender_id:
            return
        if recipient_slack == slack_user:
            await _dm(client, slack_user, "⚠️ You can't share a note with yourself.")
            return
        recipient_id = await base.resolve_user_id(client, recipient_slack)
        if not recipient_id:
            await _dm(
                client,
                slack_user,
                f"⚠️ <@{recipient_slack}> hasn't signed in to the Neurix web app yet, so I can't share with them. "
                "Ask them to sign in once with their Slack email.",
            )
            return

        def create(conn):
            node = share_service.check_shareable_node(conn, user_id=sender_id, node_id=node_id)
            existing = shares_repo.find_open_person_share(conn, node_id=node_id, recipient_id=recipient_id)
            if existing:
                state = "is still waiting for an answer" if existing["status"] == "pending" else "was already accepted"
                raise ShareError(f"Your invite to share *{escape(node['title'])}* with them {state}.")
            return shares_repo.create_person_invite(
                conn, owner_id=sender_id, recipient_id=recipient_id, node_id=node_id, message=message
            )

        try:
            share = await _db(create)
        except IntegrityError:  # lost a race with an identical invite
            raise ShareError("That note already has an open invite to this person.")

        title = share["node_title"]
        channel, ts = await _dm(
            client,
            recipient_slack,
            f"{await _name(client, slack_user)} wants to share a note with you: {title}",
            sb.invite_blocks(share_id=share["id"], sender_slack=slack_user, title=title, message=message),
        )
        await _db(shares_repo.merge_metadata, share["id"], {"invite_dm": {"channel": channel, "ts": ts}})
        await _dm(
            client,
            slack_user,
            f"Invite sent for {title}",
            sb.invite_sent_blocks(share_id=share["id"], recipient_slack=recipient_slack, title=title),
        )
    except ShareError as exc:
        await _dm(client, slack_user, f"⚠️ {exc}")
    except Exception as exc:
        logger.error("SLACK_SHARE_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await _dm(client, slack_user, "⚠️ Sorry, I couldn't send that share. Please try again.")


async def submit_share_team(*, slack_user: str, node_id: str, scope: str, targets_slack: list[str]) -> None:
    client = base.slack_client()
    try:
        user_id = await _linked_user(client, slack_user)
        if not user_id:
            return
        scope = scope if scope in NOTIFY_SCOPES else "admin"
        target_ids: list[str] = []
        if scope == "specific":
            for target in targets_slack:
                uid = await base.resolve_user_id(client, target)
                if uid:
                    target_ids.append(uid)

        def create(conn):
            membership = orgs_repo.get_membership(conn, user_id)
            if not membership:
                raise ShareError(NOT_IN_TEAM)
            node = share_service.check_shareable_node(conn, user_id=user_id, node_id=node_id)
            if shares_repo.find_team_share(conn, node_id=node_id, org_id=membership["org_id"]):
                raise ShareError(f"*{escape(node['title'])}* is already in the team space.")
            share = shares_repo.create_team_share(
                conn,
                owner_id=user_id,
                org_id=membership["org_id"],
                node_id=node_id,
                metadata={"notify_scope": scope, "notify_targets": target_ids},
            )
            return share, membership

        try:
            share, membership = await _db(create)
        except IntegrityError:
            raise ShareError("That note is already in the team space.")

        result = await asyncio.to_thread(share_service.persist_share_connections, share["id"])
        sharer_name = await _name(client, slack_user)
        title = share["node_title"]

        def notify(conn):
            recipients = share_service.team_notify_recipients(conn, share)
            notifications_repo.create_many(
                conn,
                user_ids=recipients,
                type=notifications_repo.TEAM_NODE_ADDED,
                message=f"{sharer_name} added '{title}' to the team space",
                share_node_id=share["id"],
                org_id=share["org_id"],
                payload={"node_id": share["node_id"], "connections": len(result.connected)},
            )

        await _db(notify)
        cross = result.cross_member
        await _dm(
            client,
            slack_user,
            f"✅ Added *{escape(title)}* to team *{escape(membership['name'])}* — "
            f"{len(result.connected)} connection(s), {len(cross)} with other members' notes.",
        )
        await _dm_cross_member(client, result, sharer_slack=slack_user)
    except ShareError as exc:
        await _dm(client, slack_user, f"⚠️ {exc}")
    except Exception as exc:
        logger.error("SLACK_SHARE_TEAM_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await _dm(client, slack_user, "⚠️ Sorry, I couldn't add that note to the team. Please try again.")


async def _dm_cross_member(client: SlackClient, result: share_service.PersistResult, *, sharer_slack: str) -> None:
    """The always-on "aha" DM for a team share: every newly formed
    connection between two different people's team notes, to both people."""
    cross = result.cross_member
    if not cross:
        return
    title = escape(result.share["node_title"])
    await _dm(
        client,
        sharer_slack,
        f"🔗 New cross-team connection: your *{title}* links to " + sb.title_list(cross),
    )
    by_owner: dict[str, list[dict[str, Any]]] = {}
    for node in cross:
        by_owner.setdefault(node["owner_id"], []).append(node)
    for owner_id, nodes in by_owner.items():
        owner_slack = await slack_user_for(client, owner_id)
        if not owner_slack:
            logger.warning("CROSS_MEMBER_DM_SKIPPED | user_id=%s | reason=no slack user", owner_id)
            continue
        await _dm(
            client,
            owner_slack,
            f"🔗 New cross-team connection: <@{sharer_slack}>'s *{title}* links to your " + sb.title_list(nodes),
        )


# --------------------------------------------------------------------------
# Invite buttons: Accept / Decline (recipient), Cancel (sender)
# --------------------------------------------------------------------------

async def handle_share_action(*, action_id: str, share_id: str, slack_user: str, channel: str, message_ts: str) -> None:
    client = base.slack_client()
    user_id = await base.resolve_user_id(client, slack_user)
    if not user_id:
        await client.post_ephemeral(channel, slack_user, base.NOT_LINKED)
        return
    share = await _db(shares_repo.get_share, share_id)
    if share is None:
        await client.post_ephemeral(channel, slack_user, "This invite no longer exists (it may have been cancelled).")
        return

    if action_id == sb.CANCEL_ACTION:
        await _cancel(client, share, user_id=user_id, slack_user=slack_user, channel=channel, message_ts=message_ts)
        return
    if share["shared_user_id"] != user_id:
        await client.post_ephemeral(channel, slack_user, "This invite isn't addressed to you.")
        return

    title = share["node_title"]
    if action_id == sb.DECLINE_ACTION:
        declined = await _db(shares_repo.decline_invite, share_id)
        if declined is None:
            await client.post_ephemeral(channel, slack_user, "You already answered this invite.")
            return
        name = await _name(client, slack_user)
        await _db(
            notifications_repo.create,
            user_id=share["owner_user_id"],
            type=notifications_repo.SHARE_DECLINED,
            message=f"{name} declined your share of '{title}'",
            share_node_id=share_id,
        )
        await client.update_message(channel, message_ts, "Declined.", sb.text_blocks(f"Declined *{escape(title)}*."))
        return

    accepted = await _db(shares_repo.accept_invite, share_id)
    if accepted is None:
        await client.post_ephemeral(channel, slack_user, "You already answered this invite.")
        return
    name = await _name(client, slack_user)
    await _db(
        notifications_repo.create,
        user_id=share["owner_user_id"],
        type=notifications_repo.SHARE_ACCEPTED,
        message=f"{name} accepted your share of '{title}'",
        share_node_id=share_id,
    )
    merging = f"✅ Accepted *{escape(title)}* — merging it into your network..."
    await client.update_message(channel, message_ts, "Accepted.", sb.text_blocks(merging))
    try:
        result = await asyncio.to_thread(share_service.persist_share_connections, share_id)
    except Exception as exc:
        logger.error("SHARE_PERSIST_FAILED | share_id=%s | error=%s", share_id, exc, exc_info=True)
        failed = f"✅ Accepted *{escape(title)}*, but connecting it to your notes failed."
        await client.update_message(channel, message_ts, "Accepted.", sb.text_blocks(failed))
        return

    n = len(result.connected)
    done = f"✅ *{escape(title)}* joined your network — {n} new connection{'' if n == 1 else 's'}"
    if n:
        done += " with " + sb.title_list(result.connected)
    await client.update_message(channel, message_ts, "Joined your network.", sb.text_blocks(done))
    await _db(
        notifications_repo.create,
        user_id=user_id,
        type=notifications_repo.SHARE_CONNECTIONS,
        message=f"'{title}' joined your network — {n} new connection{'' if n == 1 else 's'}",
        share_node_id=share_id,
        payload={"node_id": share["node_id"], "connections": n},
    )
    # Cross-member "aha" DM to the sender. The recipient's side is the card
    # above; the sender only learns how many notes it touched, never the
    # titles of the recipient's private notes.
    if n:
        sender_slack = await slack_user_for(client, share["owner_user_id"])
        if sender_slack:
            await _dm(
                client,
                sender_slack,
                f"🔗 Your note *{escape(title)}* connected to {n} note{'' if n == 1 else 's'} "
                f"in <@{slack_user}>'s network.",
            )


async def _cancel(client: SlackClient, share: dict[str, Any], *, user_id: str, slack_user: str, channel: str, message_ts: str) -> None:
    if share["owner_user_id"] != user_id:
        await client.post_ephemeral(channel, slack_user, "Only the sender can cancel this invite.")
        return
    cancelled = await _db(lambda conn: shares_repo.cancel_invite(conn, share_id=share["id"], owner_id=user_id))
    if cancelled is None:
        await client.post_ephemeral(
            channel, slack_user, "This invite was already answered — an accepted share can't be undone."
        )
        return
    title = escape(share["node_title"])
    await client.update_message(channel, message_ts, "Invite cancelled.", sb.text_blocks(f"Invite for *{title}* cancelled."))
    invite = (share["metadata"] or {}).get("invite_dm") or {}
    if invite.get("channel") and invite.get("ts"):
        try:
            await client.update_message(
                invite["channel"], invite["ts"], "Invite cancelled.", sb.text_blocks(f"The invite for *{title}* was cancelled.")
            )
        except Exception:
            logger.exception("SHARE_CANCEL_UPDATE_FAILED | share_id=%s", share["id"])


# --------------------------------------------------------------------------
# /neurix stats
# --------------------------------------------------------------------------

async def handle_stats(*, slack_user: str, response_url: str) -> None:
    client = base.slack_client()
    try:
        user_id = await _linked_user(client, slack_user, response_url)
        if not user_id:
            return
        data = await asyncio.to_thread(insights.collect, user_id)
        await _reply(response_url, "Your Neurix stats", insights.format_blocks(data))
    except Exception as exc:
        logger.error("SLACK_STATS_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await _reply(response_url, "⚠️ Sorry, I couldn't load your stats. Please try again.")


# --------------------------------------------------------------------------
# /neurix raw-access and /neurix file — both pick the note from a modal
# (the same note picker as /neurix share), never from typed titles.
# --------------------------------------------------------------------------

def _raw_access_state(conn, user_id: str) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], str | None]:
    """The caller's READY notes that have at least one live share, their
    shares by node id, and the caller's team name."""
    shares: dict[str, list[dict[str, Any]]] = {}
    for share in shares_repo.active_shares_by_owner(conn, user_id):
        shares.setdefault(share["node_id"], []).append(share)
    nodes = [n for n in nodes_repo.recent_ready_nodes(conn, user_id) if n["id"] in shares]
    membership = orgs_repo.get_membership(conn, user_id)
    return nodes, shares, membership["name"] if membership else None


async def _share_targets(client: SlackClient, shares: list[dict[str, Any]], team_name: str | None) -> list[tuple[str, str]]:
    """(value, label) per live share of one note: `team`, or the recipient's user_id."""
    emails = await _db(users_repo.get_emails, [s["shared_user_id"] for s in shares if s["shared_user_id"]])
    targets = []
    for share in shares:
        state = "file visible" if share["can_view_raw"] else "file hidden"
        if share["org_id"]:
            targets.append(("team", f"Team {team_name or ''}".strip() + f" — {state}"))
            continue
        recipient = share["shared_user_id"]
        slack_id = await slack_user_for(client, recipient)
        name = (await _name(client, slack_id)) if slack_id else emails.get(recipient, "Someone")
        targets.append((recipient, f"{name} — {state}"))
    return targets


async def open_raw_access(*, slack_user: str, trigger_id: str, response_url: str) -> None:
    client = base.slack_client()
    user_id = await _linked_user(client, slack_user, response_url)
    if not user_id:
        return
    nodes, _, _ = await _db(_raw_access_state, user_id)
    if not nodes:
        await _reply(
            response_url,
            "None of your notes are shared yet. Share one with `/neurix share` or `/neurix share-team` first.",
        )
        return
    await client.open_view(trigger_id, sb.raw_access_modal(nodes))
    await _reply(response_url, "📝 Opened.")  # delete_original fails with `no_text` on slash commands


async def update_raw_access_view(*, slack_user: str, view_id: str, view_hash: str | None, node_id: str) -> None:
    """A note was picked in the raw-access modal: show who it's shared with."""
    client = base.slack_client()
    user_id = await base.resolve_user_id(client, slack_user)
    if not user_id:
        return
    nodes, shares, team_name = await _db(_raw_access_state, user_id)
    targets = await _share_targets(client, shares.get(node_id, []), team_name)
    await client.update_view(view_id, sb.raw_access_modal(nodes, node_id=node_id, targets=targets), view_hash)


async def submit_raw_access(*, slack_user: str, node_id: str, target: str, on: bool) -> None:
    client = base.slack_client()
    try:
        user_id = await _linked_user(client, slack_user)
        if not user_id:
            return
        if not node_id or not target:
            await _dm(client, slack_user, "⚠️ Pick a note and who it's shared with, then try `/neurix raw-access` again.")
            return
        is_team = target == "team"
        target_id = None if is_team else target

        def toggle(conn):
            node = nodes_repo.get_node(conn, node_id)
            if node is None or node["user_id"] != user_id:
                raise ShareError("You can only change access to your own notes.")
            membership = orgs_repo.get_membership(conn, user_id)
            if is_team and not membership:
                raise ShareError(NOT_IN_TEAM)
            share = shares_repo.find_active_share(
                conn,
                node_id=node_id,
                recipient_id=target_id,
                org_id=membership["org_id"] if is_team else None,
            )
            title = node.get("title") or "Untitled"
            if share is None:
                who = "the team" if is_team else "that person"
                raise ShareError(f"*{escape(title)}* has no active share with {who}.")
            shares_repo.set_can_view_raw(conn, share["id"], on)
            if is_team:
                affected = [m for m in orgs_repo.member_ids(conn, share["org_id"]) if m != user_id]
            else:
                affected = [target_id]
            return title, share, affected

        title, share, affected = await _db(toggle)
        name = await _name(client, slack_user)
        verb = "gave you access to" if on else "removed your access to"
        await _db(
            notifications_repo.create_many,
            user_ids=affected,
            type=notifications_repo.RAW_ACCESS_GRANTED if on else notifications_repo.RAW_ACCESS_REVOKED,
            message=f"{name} {verb} the original file of '{title}'",
            share_node_id=share["id"],
            org_id=share["org_id"],
        )
        if is_team:
            who = "the team"
        else:
            target_slack = await slack_user_for(client, target_id)
            who = f"<@{target_slack}>" if target_slack else "them"
        await _dm(
            client,
            slack_user,
            f"{'🔓' if on else '🔒'} Original file of *{escape(title)}* is now "
            f"{'visible to' if on else 'hidden from'} {who}.",
        )
    except ShareError as exc:
        await _dm(client, slack_user, f"⚠️ {exc}")
    except Exception as exc:
        logger.error("SLACK_RAW_ACCESS_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await _dm(client, slack_user, "⚠️ Sorry, I couldn't change that. Please try again.")


async def open_file(*, slack_user: str, trigger_id: str, response_url: str) -> None:
    client = base.slack_client()
    user_id = await _linked_user(client, slack_user, response_url)
    if not user_id:
        return
    nodes = await _db(raw_files.fileable_nodes, user_id)
    if not nodes:
        await _reply(response_url, "There are no notes with an original file you can open yet.")
        return
    await client.open_view(trigger_id, sb.file_modal(nodes))
    await _reply(response_url, "📝 Opened.")  # delete_original fails with `no_text` on slash commands


async def submit_file(*, slack_user: str, node_id: str) -> None:
    client = base.slack_client()
    try:
        user_id = await _linked_user(client, slack_user)
        if not user_id:
            return
        # Access is re-checked at submit: it may have been revoked since the modal opened.
        node = await _db(raw_files.find_fileable, user_id=user_id, node_id=node_id)
        if node is None:
            await _dm(client, slack_user, "🔒 You no longer have access to that note's original file.")
            return
        url, ttl = await asyncio.to_thread(raw_files.signed_link, node)
        await _dm(
            client,
            slack_user,
            f"📄 Original of *{escape(node['title'])}*: <{url}|download> (link expires in {ttl // 60} min)",
        )
    except Exception as exc:
        logger.error("SLACK_FILE_URL_FAILED | slack_user=%s | error=%s", slack_user, exc, exc_info=True)
        await _dm(client, slack_user, "⚠️ Sorry, I couldn't get that file. Please try again.")
