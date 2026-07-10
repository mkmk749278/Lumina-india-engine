"""Tests for the FCM dispatcher and token registration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.db import close_db
from src.fcm_dispatcher import (
    _build_data,
    _build_notification,
    _token_cache,
    dispatch,
    init_fcm_tables,
    register_token,
    remove_token,
)
from src.signal_store import init_tables
from src.signals.model import IndiaSignal


@pytest.fixture(autouse=True)
async def _setup_db(tmp_path, monkeypatch):
    import src.db

    src.db._conn = None
    src.db._DB_DIR = tmp_path
    src.db._DB_PATH = tmp_path / "india_db.sqlite3"
    await init_tables()
    await init_fcm_tables()
    _token_cache.clear()
    yield
    await close_db()


def _sig(**overrides) -> IndiaSignal:
    defaults = dict(
        signal_id="sig-001",
        symbol="NSE:NIFTY26JULFUT",
        base="NIFTY",
        direction="LONG",
        setup_class="OPENING_RANGE_BREAKOUT",
        entry=24500.0,
        sl=24400.0,
        tp1=24700.0,
        sl_pct=0.41,
        tp1_pct=0.82,
        rr_ratio=2.0,
        lot_size=75,
        confidence=85.0,
        tier="A+",
        dispatch_timestamp=1719900000.0,
    )
    defaults.update(overrides)
    return IndiaSignal(**defaults)


async def test_register_and_retrieve_token():
    await register_token("token-abc-123456789012345678", uid="user1")
    from src.fcm_dispatcher import _get_all_tokens

    tokens = await _get_all_tokens()
    assert "token-abc-123456789012345678" in tokens


async def test_register_duplicate_updates():
    await register_token("token-dup-123456789012345678", uid="user1")
    await register_token("token-dup-123456789012345678", uid="user2")
    from src.fcm_dispatcher import _get_all_tokens

    tokens = await _get_all_tokens()
    assert tokens.count("token-dup-123456789012345678") == 1


async def test_remove_token():
    await register_token("token-del-123456789012345678", uid="user1")
    await remove_token("token-del-123456789012345678")
    from src.fcm_dispatcher import _get_all_tokens

    tokens = await _get_all_tokens()
    assert "token-del-123456789012345678" not in tokens


def test_build_notification_no_price():
    sig = _sig()
    notif = _build_notification(sig)
    assert "NIFTY" in notif["title"]
    assert "LONG" in notif["title"]
    assert "A+" in notif["body"]
    assert "24500" not in notif["body"]
    assert "24400" not in notif["body"]
    assert "24700" not in notif["body"]


def test_build_data_has_required_fields():
    sig = _sig()
    data = _build_data(sig)
    assert data["signal_id"] == "sig-001"
    assert data["symbol"] == "NSE:NIFTY26JULFUT"
    assert data["direction"] == "LONG"
    assert data["confidence_tier"] == "A+"


async def test_dispatch_no_firebase(monkeypatch):
    """Dispatch gracefully returns 0 when Firebase isn't initialized."""
    import src.fcm_dispatcher

    monkeypatch.setattr(src.fcm_dispatcher, "_initialized", False)
    monkeypatch.setattr(src.fcm_dispatcher, "_fcm_app", None)
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT_JSON", raising=False)
    result = await dispatch(_sig())
    assert result == 0


async def test_dispatch_no_tokens(monkeypatch):
    """Dispatch returns 0 when Firebase is init'd but no tokens registered."""
    import src.fcm_dispatcher

    monkeypatch.setattr(src.fcm_dispatcher, "_initialized", True)
    monkeypatch.setattr(src.fcm_dispatcher, "_fcm_app", MagicMock())
    result = await dispatch(_sig())
    assert result == 0


async def test_dispatch_sends_to_registered_tokens(monkeypatch):
    """Dispatch sends one FCM message per registered token."""
    from types import SimpleNamespace

    import src.fcm_dispatcher

    # Sends go through the batch API (send_each) off the event loop — the
    # per-token synchronous send() froze the loop for the whole fan-out.
    batch = SimpleNamespace(
        responses=[SimpleNamespace(success=True, exception=None)]
    )
    mock_send_each = MagicMock(return_value=batch)
    mock_messaging = MagicMock()
    mock_messaging.send_each = mock_send_each
    mock_messaging.Message = MagicMock()
    mock_messaging.Notification = MagicMock()
    mock_messaging.AndroidConfig = MagicMock()
    mock_messaging.AndroidNotification = MagicMock()
    mock_messaging.UnregisteredError = type("UnregisteredError", (Exception,), {})

    monkeypatch.setattr(src.fcm_dispatcher, "_initialized", True)
    monkeypatch.setattr(src.fcm_dispatcher, "_fcm_app", MagicMock())
    monkeypatch.setattr(src.fcm_dispatcher, "_messaging", mock_messaging)

    await register_token("token-send-12345678901234567", uid="u1")

    result = await dispatch(_sig())

    assert result == 1
    mock_send_each.assert_called_once()
