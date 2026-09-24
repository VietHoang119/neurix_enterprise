"""Slack request signing (https://api.slack.com/authentication/verifying-requests-from-slack).

Every /slack/* request is checked here before anything else is parsed or
acted on — this is the only thing stopping a forged "Confirm" click.
"""

import hashlib
import hmac
import time

# Slack's recommended replay window.
MAX_CLOCK_SKEW_SECONDS = 60 * 5


def is_valid_slack_signature(
    *,
    signing_secret: str | None,
    body: bytes,
    timestamp: str | None,
    signature: str | None,
    now: float | None = None,
) -> bool:
    if not signing_secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - ts) > MAX_CLOCK_SKEW_SECONDS:
        return False

    basestring = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
