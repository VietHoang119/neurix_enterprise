"""`/neurix stats`: unread notification digest + personal and org insights,
as plain Slack mrkdwn sections."""

from typing import Any

from db.session import get_engine
from repositories import notifications as notifications_repo
from repositories import orgs as orgs_repo
from repositories import stats as stats_repo
from services.slack.blocks import escape


def collect(user_id: str) -> dict[str, Any]:
    """Read everything the digest shows, marking the unread notifications
    read in the same transaction."""
    with get_engine().begin() as conn:
        membership = orgs_repo.get_membership(conn, user_id)
        notifications, unread = notifications_repo.take_unread(conn, user_id)
        return {
            "notifications": notifications,
            "unread": unread,
            "personal": stats_repo.personal(conn, user_id),
            "org": stats_repo.org(conn, membership["org_id"]) if membership else None,
            "org_name": membership["name"] if membership else None,
        }


def _s(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def format_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    def section(text: str) -> None:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})

    notes = data["notifications"]
    if notes:
        lines = [f"• {n['message']}" for n in notes]
        if data["unread"] > len(notes):
            lines.append(f"_…and {data['unread'] - len(notes)} older_")
        section("*🔔 Since last time*\n" + "\n".join(lines))
    else:
        section("*🔔 Since last time*\nNothing new.")

    p = data["personal"]
    section(
        "*👤 You*\n"
        f"• *Volume:* {_s(p['nodes'], 'node')}, {_s(p['keys'], 'key')}, {_s(p['connections'], 'connection')}\n"
        f"• *Shared:* {p['shared_to_people']} with people"
        + (f" ({p['pending_invites']} pending)" if p["pending_invites"] else "")
        + f", {p['shared_to_team']} to your team · {p['received']} received\n"
        f"• *Quality:* {p['orphan_pct']}% unconnected nodes, {p['avg_keys']} keys per node\n"
        f"• *Bridges:* {_s(p['bridges'], 'connection')} between your nodes and other people's\n"
        f"• *Impact:* your nodes were used {_s(p['impact'], 'time')} in answers"
    )

    o = data["org"]
    if o is None:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": "Not in a team yet — `/neurix team create <name>`."}]}
        )
        return blocks

    lines = [
        f"*👥 Team {escape(data['org_name'])}*",
        f"• *Volume:* {_s(o['members'], 'member')}, {_s(o['nodes'], 'shared node')} from "
        f"{_s(o['contributors'], 'contributor')}, {_s(o['keys'], 'key')}, {_s(o['connections'], 'connection')}",
        f"• *Quality:* {o['orphan_pct']}% unconnected team nodes, {o['avg_keys']} keys per node",
        f"• *Cross-contribution:* {_s(o['bridges'], 'bridge')} between different members' nodes",
        f"• *Impact:* team nodes were used {_s(o['impact'], 'time')} in team answers",
    ]
    if o["expertise"]:
        lines.append("• *Expertise:* " + "; ".join(
            f"{escape(owner)} — {', '.join(escape(k) for k in keys)}" for owner, keys in o["expertise"].items()
        ))
    section("\n".join(lines))

    if o["bus_factor"]:
        section(
            "*⚠️ Bus factor*\n"
            + "\n".join(
                f"• *{escape(d['key'])}* ({_s(d['nodes'], 'node')}) — only {escape(d['owner'])} contributes here"
                for d in o["bus_factor"]
            )
        )
    return blocks
