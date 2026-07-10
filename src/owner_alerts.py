"""Owner engine-health alerts — the escalation channel that was missing.

Every incident so far (Sessions 8c, 10, 15, 16) failed *silently* and was
caught by the owner noticing the app looked wrong, hours late. This module
makes silence impossible for the conditions that matter: it pushes an FCM
notification (distinct "engine-alerts" Android channel, never confusable
with a signal) to the owner's device when the engine degrades.

Alert kinds are rate-limited individually (OWNER_ALERT_COOLDOWN_SEC) so a
flapping feed cannot turn the owner's phone into a siren. Best-effort by
design: an alert failure is logged and never disturbs the engine.
"""

from __future__ import annotations

import time

import config
from src import fcm_dispatcher
from src.utils import get_logger

logger = get_logger("owner_alerts")

_last_sent: dict[str, float] = {}


def _should_send(kind: str) -> bool:
    now = time.monotonic()
    last = _last_sent.get(kind)
    if last is not None and now - last < config.OWNER_ALERT_COOLDOWN_SEC:
        return False
    _last_sent[kind] = now
    return True


async def alert(kind: str, title: str, body: str) -> None:
    """Send a rate-limited engine-health alert to the owner. Never raises."""
    if not _should_send(kind):
        logger.info("owner alert [{}] suppressed by cooldown", kind)
        return
    try:
        await fcm_dispatcher.dispatch_owner_alert(title, body, kind)
    except Exception:
        logger.opt(exception=True).error("owner alert [{}] failed to send", kind)


def reset() -> None:
    """Clear rate-limit state (tests)."""
    _last_sent.clear()
