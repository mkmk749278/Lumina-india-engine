"""FCM push notification dispatcher.

Sends a push notification to all registered device tokens when a signal
is emitted.  This is the "doorbell" — the app then fetches full signal
detail from the REST API.

Notification body never contains price targets (CLAUDE.md / OWNER_BRIEF):
only symbol, direction, and confidence tier.  Subscribers see detail
in-app behind the auth/subscription wall.

Cost: one FCM send per signal × registered tokens.  Free at any volume
and never on a hot path (not per-tick, not per-scan).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.signals.model import IndiaSignal
from src.utils import get_logger

logger = get_logger("fcm")

_fcm_app: Any = None
_messaging: Any = None
_initialized = False


def _load_firebase_sa() -> dict | None:
    """Load Firebase service account from file or env var.

    Prefers the JSON file (avoids .env quoting issues with embedded JSON).
    Falls back to the env var for backwards compatibility.
    """
    sa_file = os.environ.get("FIREBASE_SERVICE_ACCOUNT_FILE", "/app/firebase-sa.json")
    if os.path.isfile(sa_file):
        with open(sa_file) as f:
            return dict(json.load(f))
    raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if raw:
        return dict(json.loads(raw))
    return None


def _init_firebase() -> bool:
    """Lazily initialize the Firebase Admin SDK for FCM.

    Returns True if initialization succeeded, False otherwise.
    The SDK is initialized at most once per process.
    """
    global _fcm_app, _messaging, _initialized
    if _initialized:
        return _fcm_app is not None

    _initialized = True

    try:
        import firebase_admin
        from firebase_admin import messaging

        try:
            _fcm_app = firebase_admin.get_app()
            _messaging = messaging
            logger.info("Firebase Admin SDK reused for FCM")
            return True
        except ValueError:
            pass

        sa_data = _load_firebase_sa()
        if sa_data is None:
            logger.warning("No Firebase credentials found — FCM disabled")
            return False

        from firebase_admin import credentials

        cred = credentials.Certificate(sa_data)
        _fcm_app = firebase_admin.initialize_app(cred)
        _messaging = messaging
        logger.info("Firebase Admin SDK initialized for FCM")
        return True
    except Exception:
        logger.opt(exception=True).error("Firebase init failed — FCM disabled")
        return False


# --- Token storage (SQLite) ------------------------------------------------

_FCM_TOKENS_DDL = """
CREATE TABLE IF NOT EXISTS india_fcm_tokens (
    token      TEXT PRIMARY KEY,
    uid        TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


async def init_fcm_tables() -> None:
    from src.db import get_db

    db = await get_db()
    await db.execute(_FCM_TOKENS_DDL)
    await db.commit()


async def register_token(token: str, uid: str = "") -> None:
    """Insert or update an FCM device token."""
    from src.db import get_db

    db = await get_db()
    await db.execute(
        """
        INSERT INTO india_fcm_tokens (token, uid, updated_at)
        VALUES (?, ?, datetime('now', 'localtime'))
        ON CONFLICT(token) DO UPDATE SET
            uid = excluded.uid,
            updated_at = excluded.updated_at
        """,
        (token, uid),
    )
    await db.commit()
    _token_cache.clear()
    logger.info("FCM token registered (uid={})", uid or "anon")


async def remove_token(token: str) -> None:
    """Delete an invalid/expired FCM token."""
    from src.db import get_db

    db = await get_db()
    await db.execute("DELETE FROM india_fcm_tokens WHERE token = ?", (token,))
    await db.commit()
    _token_cache.clear()


_token_cache: list[str] = []
_token_cache_gen: int = 0


async def _get_all_tokens() -> list[str]:
    """Return all registered FCM tokens (cached in-memory, invalidated on write)."""
    global _token_cache_gen
    if _token_cache:
        return _token_cache

    from src.db import get_db

    db = await get_db()
    cursor = await db.execute("SELECT token FROM india_fcm_tokens")
    rows = await cursor.fetchall()
    tokens = [str(row[0]) for row in rows]
    _token_cache.clear()
    _token_cache.extend(tokens)
    _token_cache_gen += 1
    return tokens


# --- Dispatch ---------------------------------------------------------------


def _build_notification(sig: IndiaSignal) -> dict:
    """Build the FCM message payload for a signal.

    Notification body: "NIFTY LONG — A+ confidence"
    Data payload includes signal_id for deep-link navigation.
    """
    return {
        "title": f"{sig.base} {sig.direction} Signal",
        "body": f"{sig.base} {sig.direction} — {sig.tier} confidence",
    }


def _build_data(sig: IndiaSignal) -> dict[str, str]:
    """Data payload for client-side routing (tap → signal detail screen)."""
    return {
        "signal_id": sig.signal_id,
        "symbol": sig.symbol,
        "base": sig.base,
        "direction": sig.direction,
        "confidence_tier": sig.tier,
        "setup_class": sig.setup_class,
    }


# FCM's batch API accepts up to 500 messages per call.
_FCM_BATCH_SIZE = 500


async def _send_batch(
    tokens: list[str],
    notification: Any,
    data: dict[str, str],
    channel_id: str,
) -> int:
    """Send one message per token via the batch API, off the event loop.

    The previous implementation called the *synchronous* ``messaging.send``
    once per token on the event loop — one blocking HTTPS round trip per
    subscriber, freezing the scan loop and the API for the whole fan-out
    (at 100 subscribers: ~15-40s of dead engine per signal). ``send_each``
    is one round trip per 500 tokens, and ``asyncio.to_thread`` keeps even
    that off the loop. Stale tokens are pruned from the batch responses.
    Best-effort: failures are logged, never raised.
    """
    android = _messaging.AndroidConfig(
        priority="high",
        notification=_messaging.AndroidNotification(
            channel_id=channel_id,
            priority="max",
        ),
    )
    sent = 0
    stale_tokens: list[str] = []
    for i in range(0, len(tokens), _FCM_BATCH_SIZE):
        chunk = tokens[i : i + _FCM_BATCH_SIZE]
        messages = [
            _messaging.Message(
                notification=notification,
                data=data,
                token=token,
                android=android,
            )
            for token in chunk
        ]
        try:
            batch = await asyncio.to_thread(
                _messaging.send_each, messages, app=_fcm_app
            )
        except Exception:
            logger.opt(exception=True).warning(
                "FCM batch send failed ({} tokens)", len(chunk)
            )
            continue
        for token, resp in zip(chunk, batch.responses, strict=True):
            if resp.success:
                sent += 1
            elif isinstance(resp.exception, _messaging.UnregisteredError):
                stale_tokens.append(token)
            else:
                logger.warning(
                    "FCM send failed for token ...{}: {}",
                    token[-8:],
                    resp.exception,
                )

    for stale in stale_tokens:
        await remove_token(stale)
        logger.info("removed stale FCM token ...{}", stale[-8:])
    return sent


async def dispatch(sig: IndiaSignal) -> int:
    """Send FCM push to all registered tokens. Returns count of messages sent."""
    if not _init_firebase():
        return 0

    tokens = await _get_all_tokens()
    if not tokens:
        logger.debug("no FCM tokens registered — skipping push")
        return 0

    notification = _messaging.Notification(**_build_notification(sig))
    sent = await _send_batch(
        tokens, notification, _build_data(sig), channel_id="signals"
    )
    logger.info(
        "FCM dispatched {} {} — sent to {}/{} tokens",
        sig.base,
        sig.direction,
        sent,
        len(tokens),
    )
    return sent


async def dispatch_owner_alert(title: str, body: str, kind: str) -> int:
    """Engine-health alert to the owner's device(s) (src/owner_alerts.py).

    Separate Android channel ("engine-alerts") so it never looks like a
    trading signal. Phase 1 has one user; when INDIA_OWNER_UIDS is set only
    tokens registered under those UIDs receive alerts — set it before
    subscriber onboarding.
    """
    import config

    if not _init_firebase():
        return 0

    from src.db import get_db

    db = await get_db()
    if config.OWNER_ALERT_UIDS:
        placeholders = ",".join("?" for _ in config.OWNER_ALERT_UIDS)
        cursor = await db.execute(
            f"SELECT token FROM india_fcm_tokens WHERE uid IN ({placeholders})",
            config.OWNER_ALERT_UIDS,
        )
    else:
        cursor = await db.execute("SELECT token FROM india_fcm_tokens")
    tokens = [str(row[0]) for row in await cursor.fetchall()]
    if not tokens:
        logger.warning("owner alert '{}' — no FCM tokens registered", kind)
        return 0

    notification = _messaging.Notification(title=title, body=body)
    data = {"alert_kind": kind, "type": "engine_alert"}
    sent = await _send_batch(tokens, notification, data, channel_id="engine-alerts")
    logger.warning("OWNER ALERT [{}] sent to {} device(s): {}", kind, sent, title)
    return sent
