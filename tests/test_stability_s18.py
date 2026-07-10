"""Session-18 stability-audit implementations.

Covers: owner-alert rate limiting, the nightly DB backup (VACUUM INTO +
retention), FCM batch stale-token cleanup, the VIX staleness TTL, and the
single-writer tick handoff fallback.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import config
from src import owner_alerts
from src.data.india_market_data import IndiaMarketData

# ── Owner alerts: per-kind rate limiting ─────────────────────────────


async def test_owner_alert_rate_limited_per_kind(monkeypatch):
    owner_alerts.reset()
    sent: list[str] = []

    async def fake_dispatch(title, body, kind):  # type: ignore[no-untyped-def]
        sent.append(kind)
        return 1

    monkeypatch.setattr(
        owner_alerts.fcm_dispatcher, "dispatch_owner_alert", fake_dispatch
    )

    await owner_alerts.alert("feed_stall", "t", "b")
    await owner_alerts.alert("feed_stall", "t", "b")  # inside cooldown
    await owner_alerts.alert("feed_down", "t", "b")  # different kind — sends

    assert sent == ["feed_stall", "feed_down"]


async def test_owner_alert_resends_after_cooldown(monkeypatch):
    owner_alerts.reset()
    sent: list[str] = []

    async def fake_dispatch(title, body, kind):  # type: ignore[no-untyped-def]
        sent.append(kind)
        return 1

    monkeypatch.setattr(
        owner_alerts.fcm_dispatcher, "dispatch_owner_alert", fake_dispatch
    )
    await owner_alerts.alert("feed_stall", "t", "b")
    # Age the rate-limit entry past the cooldown.
    owner_alerts._last_sent["feed_stall"] -= config.OWNER_ALERT_COOLDOWN_SEC + 1
    await owner_alerts.alert("feed_stall", "t", "b")
    assert sent == ["feed_stall", "feed_stall"]


async def test_owner_alert_never_raises(monkeypatch):
    owner_alerts.reset()

    async def boom(title, body, kind):  # type: ignore[no-untyped-def]
        raise RuntimeError("fcm down")

    monkeypatch.setattr(
        owner_alerts.fcm_dispatcher, "dispatch_owner_alert", boom
    )
    await owner_alerts.alert("feed_stall", "t", "b")  # must not raise


# ── DB backup: VACUUM INTO + retention ───────────────────────────────


async def test_backup_writes_snapshot_and_prunes(tmp_path, monkeypatch):
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    # Point the engine DB into the temp dir and create some state.
    import src.db as db_mod

    monkeypatch.setattr(db_mod, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db_mod, "_DB_PATH", tmp_path / "india_db.sqlite3")
    monkeypatch.setattr(db_mod, "_conn", None)
    from src.signal_store import init_tables

    await init_tables()

    from src.db_backup import backup_database

    # Pre-existing old backups beyond retention get pruned.
    backups = tmp_path / "backups"
    backups.mkdir()
    for i in range(config.DB_BACKUP_KEEP + 3):
        (backups / f"india_db_2026-01-{i + 1:02d}.sqlite3").write_bytes(b"x")

    target = await backup_database()

    assert target is not None and target.exists()
    assert target.stat().st_size > 0  # a real SQLite file, not a stub
    remaining = sorted(backups.glob("india_db_*.sqlite3"))
    assert len(remaining) == config.DB_BACKUP_KEEP
    assert remaining[-1] == target  # newest kept

    await db_mod.close_db()


async def test_backup_failure_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path / "nope"))
    import src.db_backup as bk

    async def boom():  # type: ignore[no-untyped-def]
        raise RuntimeError("db gone")

    monkeypatch.setattr(bk, "get_db", boom)
    assert await bk.backup_database() is None  # logged, never raised


# ── FCM batch: stale tokens pruned from batch responses ──────────────


async def test_batch_send_prunes_unregistered_tokens(monkeypatch):
    import src.fcm_dispatcher as fcm

    unregistered = type("UnregisteredError", (Exception,), {})
    batch = SimpleNamespace(
        responses=[
            SimpleNamespace(success=True, exception=None),
            SimpleNamespace(success=False, exception=unregistered()),
        ]
    )
    mock_messaging = MagicMock()
    mock_messaging.send_each = MagicMock(return_value=batch)
    mock_messaging.UnregisteredError = unregistered
    monkeypatch.setattr(fcm, "_messaging", mock_messaging)
    monkeypatch.setattr(fcm, "_fcm_app", MagicMock())

    removed: list[str] = []

    async def fake_remove(token):  # type: ignore[no-untyped-def]
        removed.append(token)

    monkeypatch.setattr(fcm, "remove_token", fake_remove)

    sent = await fcm._send_batch(
        ["good-token", "dead-token"], MagicMock(), {}, channel_id="signals"
    )
    assert sent == 1
    assert removed == ["dead-token"]


# ── VIX staleness TTL ────────────────────────────────────────────────


def test_vix_reads_zero_before_first_update() -> None:
    assert IndiaMarketData().get_vix() == 0.0


def test_vix_fresh_value_passes_through() -> None:
    mkt = IndiaMarketData()
    mkt.update_vix(15.5)
    assert mkt.get_vix() == 15.5


def test_vix_stale_reads_unavailable() -> None:
    mkt = IndiaMarketData()
    mkt.update_vix(24.9)
    mkt._vix_mono = time.monotonic() - (config.VIX_TTL_SEC + 1)
    assert mkt.get_vix() == 0.0


# ── Single-writer handoff: inline fallback without a loop ────────────


def test_fyers_tick_ingests_inline_without_loop() -> None:
    from src.broker.fyers_feed import FyersDataFeed
    from src.data.india_market_data import IndiaMarketData as MD
    from src.data.india_oi_store import IndiaOIStore
    from src.data.india_tick_store import IndiaTickStore
    from src.session.expiry_manager import ExpiryManager

    tick = IndiaTickStore()
    feed = FyersDataFeed(tick, IndiaOIStore(), MD(), ExpiryManager())
    # No event loop captured (tests / pre-start) -> ingest happens inline.
    feed._process_tick(
        {"symbol": "NSE:NIFTY26JULFUT", "ltp": 24000.0, "vol_traded_today": 10.0}
    )
    assert tick.get_last_price("NSE:NIFTY26JULFUT") == 24000.0
