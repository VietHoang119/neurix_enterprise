"""Block Kit payloads for the preview card."""

from typing import Any

CONFIRM_ACTION = "neurix_confirm"
EDIT_ACTION = "neurix_edit"
DISCARD_ACTION = "neurix_discard"

# Slack caps section text at 3000 chars.
_MAX_SECTION_CHARS = 2900


def escape(text: str) -> str:
    """Escape the three characters Slack's mrkdwn treats as control chars."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _truncate(text: str, limit: int = _MAX_SECTION_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def preview_blocks(draft_id: str, preview: dict[str, Any]) -> list[dict[str, Any]]:
    title = preview.get("title") or "Untitled"
    summary = preview.get("summary") or ""
    keys = preview.get("keys") or []
    keys_line = ", ".join(f"`{escape(k)}`" for k in keys) if keys else "_none_"
    return [
        {"type": "header", "text": {"type": "plain_text", "text": _truncate(title, 150), "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(escape(summary)) or "_empty summary_"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _truncate(f"*Keys:* {keys_line}")}]},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": CONFIRM_ACTION,
                    "text": {"type": "plain_text", "text": "✅ Confirm", "emoji": True},
                    "style": "primary",
                    "value": draft_id,
                },
                {
                    "type": "button",
                    "action_id": EDIT_ACTION,
                    "text": {"type": "plain_text", "text": "✏️ Edit", "emoji": True},
                    "value": draft_id,
                },
                {
                    "type": "button",
                    "action_id": DISCARD_ACTION,
                    "text": {"type": "plain_text", "text": "❌ Discard", "emoji": True},
                    "style": "danger",
                    "value": draft_id,
                },
            ],
        },
    ]


def status_blocks(title: str, status_line: str) -> list[dict[str, Any]]:
    """A finished (button-less) card: node title + one status line."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _truncate(f"*{escape(title)}*\n{status_line}")}},
    ]
