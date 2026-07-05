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

import json
import os
from typing import Any

from src.signals.model import IndiaSignal
from src.utils import get_logger

logger = get_logger("fcm")

_fcm_app: Any = None
_messaging: Any = None
_initialized = False


def _init_firebase() -> bool:
    """Lazily initialize the Firebase Admin SDK from env credentials.

    Returns True if initialization succeeded, False otherwise.
    The SDK is initialized at most once per process.
    """
    global _fcm_app, _messaging, _initialized
    if _initialized:
        return _fcm_app is not None

    _initialized = True
    creds_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "")
    if not creds_json:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_JSON not set — FCM disabled")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        cred = credentials.Certificate(json.loads(creds_json))
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


async def dispatch(sig: IndiaSignal) -> int:
    """Send FCM push to all registered tokens. Returns count of messages sent.

    Best-effort: failures are logged, never raised. A failed push must not
    block signal storage or subsequent signals.
    """
    if not _init_firebase():
        return 0

    tokens = await _get_all_tokens()
    if not tokens:
        logger.debug("no FCM tokens registered — skipping push")
        return 0

    notification = _messaging.Notification(**_build_notification(sig))
    data = _build_data(sig)

    sent = 0
    stale_tokens: list[str] = []

    for token in tokens:
        msg = _messaging.Message(
            notification=notification,
            data=data,
            token=token,
            android=_messaging.AndroidConfig(
                priority="high",
                notification=_messaging.AndroidNotification(
                    channel_id="signals",
                    priority="max",
                ),
            ),
        )
        try:
            _messaging.send(msg, app=_fcm_app)
            sent += 1
        except _messaging.UnregisteredError:
            stale_tokens.append(token)
        except Exception:
            logger.opt(exception=True).warning(
                "FCM send failed for token ...{}", token[-8:]
            )

    for stale in stale_tokens:
        await remove_token(stale)
        logger.info("removed stale FCM token ...{}", stale[-8:])

    logger.info(
        "FCM dispatched {} {} — sent to {}/{} tokens",
        sig.base,
        sig.direction,
        sent,
        len(tokens),
    )
    return sent
