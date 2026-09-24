"""Block Kit payloads for team, share, and stats flows."""

from typing import Any

from services.slack.blocks import escape

# Modal callback ids (view_submission) and the block/action ids read back
# from their state.
SHARE_MODAL = "neurix_share_modal"
SHARE_TEAM_MODAL = "neurix_share_team_modal"
TEAM_INVITE_MODAL = "neurix_team_invite_modal"

NODE_BLOCK = "node"
RECIPIENT_BLOCK = "recipient"
MESSAGE_BLOCK = "message"
SCOPE_BLOCK = "notify_scope"
TARGETS_BLOCK = "notify_targets"
MEMBER_BLOCK = "member"

# Buttons (value = share_node id)
ACCEPT_ACTION = "neurix_share_accept"
DECLINE_ACTION = "neurix_share_decline"
CANCEL_ACTION = "neurix_share_cancel"
SHARE_ACTIONS = (ACCEPT_ACTION, DECLINE_ACTION, CANCEL_ACTION)

_SCOPE_LABELS = {"admin": "Admin only", "all": "Everyone", "specific": "Specific people"}
_MAX_OPTION_CHARS = 75  # Slack's limit for option text


def _plain(text: str, limit: int = 75) -> dict[str, Any]:
    text = text if len(text) <= limit else text[: limit - 1] + "…"
    return {"type": "plain_text", "text": text, "emoji": True}


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def _node_input(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "input",
        "block_id": NODE_BLOCK,
        "label": _plain("Note"),
        "element": {
            "type": "static_select",
            "action_id": NODE_BLOCK,
            "placeholder": _plain("Pick one of your notes"),
            "options": [
                {"text": _plain(n["title"], _MAX_OPTION_CHARS), "value": n["id"]} for n in nodes[:100]
            ],
        },
    }


def _modal(callback_id: str, title: str, submit: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "modal",
        "callback_id": callback_id,
        "title": _plain(title, 24),
        "submit": _plain(submit, 24),
        "close": _plain("Cancel", 24),
        "blocks": blocks,
    }


def share_modal(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return _modal(
        SHARE_MODAL,
        "Share a note",
        "Send invite",
        [
            _node_input(nodes),
            {
                "type": "input",
                "block_id": RECIPIENT_BLOCK,
                "label": _plain("Share with"),
                "element": {"type": "users_select", "action_id": RECIPIENT_BLOCK, "placeholder": _plain("Pick a person")},
            },
            {
                "type": "input",
                "block_id": MESSAGE_BLOCK,
                "optional": True,
                "label": _plain("Message"),
                "element": {"type": "plain_text_input", "action_id": MESSAGE_BLOCK, "multiline": True, "max_length": 500},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "They'll get an Accept / Decline invite. Once accepted, a share can't be undone."}
                ],
            },
        ],
    )


def share_team_modal(nodes: list[dict[str, Any]], org_name: str) -> dict[str, Any]:
    options = [{"text": _plain(label), "value": value} for value, label in _SCOPE_LABELS.items()]
    return _modal(
        SHARE_TEAM_MODAL,
        "Share to team",
        "Add to team",
        [
            _section(f"Add a note to the *{escape(org_name)}* team space. Every member can use it right away."),
            _node_input(nodes),
            {
                "type": "input",
                "block_id": SCOPE_BLOCK,
                "label": _plain("Notify"),
                "element": {
                    "type": "radio_buttons",
                    "action_id": SCOPE_BLOCK,
                    "options": options,
                    "initial_option": options[0],
                },
            },
            {
                "type": "input",
                "block_id": TARGETS_BLOCK,
                "optional": True,
                "label": _plain("Specific people (only for “Specific people”)"),
                "element": {"type": "multi_users_select", "action_id": TARGETS_BLOCK, "placeholder": _plain("Pick people")},
            },
        ],
    )


def team_invite_modal(org_name: str) -> dict[str, Any]:
    return _modal(
        TEAM_INVITE_MODAL,
        "Add to team",
        "Add",
        [
            _section(f"Add a workspace member to *{escape(org_name)}*."),
            {
                "type": "input",
                "block_id": MEMBER_BLOCK,
                "label": _plain("Member"),
                "element": {"type": "users_select", "action_id": MEMBER_BLOCK, "placeholder": _plain("Pick a person")},
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "They must have signed in to the Neurix web app once with their Slack email."}
                ],
            },
        ],
    )


def invite_blocks(*, share_id: str, sender_slack: str, title: str, message: str | None) -> list[dict[str, Any]]:
    blocks = [_section(f"<@{sender_slack}> wants to share a note with you: *{escape(title)}*")]
    if message:
        blocks.append(_section("> " + escape(message).replace("\n", "\n> ")))
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {"type": "button", "action_id": ACCEPT_ACTION, "text": _plain("✅ Accept"), "style": "primary", "value": share_id},
                {"type": "button", "action_id": DECLINE_ACTION, "text": _plain("Decline"), "style": "danger", "value": share_id},
            ],
        }
    )
    return blocks


def invite_sent_blocks(*, share_id: str, recipient_slack: str, title: str) -> list[dict[str, Any]]:
    return [
        _section(f"📨 Invite sent to <@{recipient_slack}> for *{escape(title)}*."),
        {
            "type": "actions",
            "elements": [
                {"type": "button", "action_id": CANCEL_ACTION, "text": _plain("Cancel invite"), "value": share_id},
            ],
        },
    ]


def text_blocks(text: str) -> list[dict[str, Any]]:
    """A finished (button-less) card."""
    return [_section(text)]


def title_list(nodes: list[dict[str, Any]], limit: int = 5) -> str:
    shown = ", ".join(f"*{escape(n['title'])}*" for n in nodes[:limit])
    more = len(nodes) - limit
    return shown + (f" and {more} more" if more > 0 else "")
