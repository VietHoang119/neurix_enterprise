"""Free-text edit replies to a preview card (no modal, no autocomplete — by
design). A reply is read line by line:

  +key: X     add key X
  -key: Y     remove key Y
  anything else   becomes the new summary (all such lines, joined)
"""

import re
from typing import Any

_KEY_LINE = re.compile(r"^\s*([+-])\s*key\s*:\s*(.+?)\s*$", re.IGNORECASE)


def normalize_key(key: str) -> str:
    # Same normalization KeysExtractorAgent applies to generated keys.
    return key.strip().lower()


def parse_edit(text: str) -> tuple[list[str], list[str], str | None]:
    """Return (keys_to_add, keys_to_remove, new_summary_or_None)."""
    adds: list[str] = []
    removes: list[str] = []
    summary_lines: list[str] = []
    for line in (text or "").splitlines():
        match = _KEY_LINE.match(line)
        if match:
            key = normalize_key(match.group(2))
            if key:
                (adds if match.group(1) == "+" else removes).append(key)
        else:
            summary_lines.append(line)
    summary = "\n".join(summary_lines).strip()
    return adds, removes, summary or None


def apply_edit(draft_preview: dict[str, Any], text: str) -> list[str]:
    """Apply an edit reply to a stored draft preview in place. Returns a
    human-readable list of what changed (empty if nothing did)."""
    adds, removes, summary = parse_edit(text)
    preview = draft_preview["preview"]
    keys_payload = draft_preview["keys_payload"]
    changes: list[str] = []

    if summary is not None and summary != preview.get("summary"):
        preview["summary"] = summary
        draft_preview["node_payload"]["summary"] = summary
        draft_preview["summary_edited"] = True
        changes.append("summary updated")

    keys: list[str] = list(preview.get("keys") or [])
    for key in removes:
        if key in keys:
            keys.remove(key)
            changes.append(f"removed key '{key}'")
    for key in adds:
        if key not in keys:
            keys.append(key)
            changes.append(f"added key '{key}'")
    preview["keys"] = keys
    # Typed keys are "new" (not a reused suggestion) unless they already
    # were a suggestion — they go through dedup in the background job.
    existing = [k for k in keys_payload.get("existing_keys") or [] if k in keys]
    keys_payload["existing_keys"] = existing
    keys_payload["new_keys"] = [k for k in keys if k not in existing]
    return changes
