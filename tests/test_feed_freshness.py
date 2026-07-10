"""Session-16 frozen-feed defences (live 2026-07-10 incident).

The WebSocket died silently after the morning token hot-swap: the scanner ran
all session on the static seed, emitted duplicate signals with identical
hour-old entries, outcomes never resolved and the app showed +0.00% running
P&L on every card. Three layers now stop that:

  1. Tick store tracks the newest *live* tick per symbol (seed never counts).
  2. stale_data_gate suppresses candidates whose data is frozen / seed-only.
  3. The feeds expose seconds_since_last_tick() + restart() for the main-loop
     watchdog, and the live-price overlay drops stale prices.
"""

from __future__ import annotations

import time as time_mod
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import config
from src.broker.fyers_feed import FyersDataFeed
from src.data.india_context_builder import IndiaContextBuilder
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.scanner import GateChain
from src.session.expiry_manager import ExpiryManager
from src.session.session_manager import SessionState
from tests.signal_factory import make_context, make_signal

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT"
_NOW = IST.localize(datetime(2026, 7, 10, 11, 0, 0))


def _candle(ts: datetime, price: float) -> Candle:
    return Candle(
        ts=ts, open=price, high=price, low=price, close=price, volume=1000.0
    )


# ── Tick store: live-tick timestamp ─────────────────────────────────────


def test_last_tick_ts_none_before_any_tick() -> None:
    store = IndiaTickStore()
    assert store.get_last_tick_ts(_SYM) is None


def test_seed_does_not_count_as_live_tick() -> None:
    store = IndiaTickStore()
    store.seed(_SYM, [_candle(_NOW - timedelta(minutes=10), 24000.0)])
    assert store.get_last_tick_ts(_SYM) is None


def test_on_tick_records_timestamp() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _NOW)
    assert store.get_last_tick_ts(_SYM) == _NOW


# ── Context builder stamps the age ──────────────────────────────────────


def _builder(store: IndiaTickStore) -> IndiaContextBuilder:
    return IndiaContextBuilder(
        store, IndiaOIStore(), IndiaMarketData(), ExpiryManager()
    )


def test_context_age_none_for_seed_only_data() -> None:
    store = IndiaTickStore()
    store.seed(_SYM, [_candle(_NOW - timedelta(minutes=5), 24000.0)])
    ctx = _builder(store).build(_SYM, "NIFTY", _NOW)
    assert ctx.last_tick_age_sec is None


def test_context_age_measures_from_last_tick() -> None:
    store = IndiaTickStore()
    store.on_tick(_SYM, 24000.0, 100.0, _NOW - timedelta(seconds=45))
    ctx = _builder(store).build(_SYM, "NIFTY", _NOW)
    assert ctx.last_tick_age_sec is not None
    assert 44.0 <= ctx.last_tick_age_sec <= 46.0


# ── stale_data_gate ─────────────────────────────────────────────────────


def test_stale_gate_suppresses_seed_only_data() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(last_tick_age_sec=None)
    assert chain.check(sig, ctx, SessionState.OPEN, _NOW) == "stale_data_gate"


def test_stale_gate_suppresses_frozen_data() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(last_tick_age_sec=float(config.MAX_TICK_AGE_SEC + 1))
    assert chain.check(sig, ctx, SessionState.OPEN, _NOW) == "stale_data_gate"


def test_stale_gate_passes_fresh_data() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(last_tick_age_sec=5.0)
    assert chain.check(sig, ctx, SessionState.OPEN, _NOW) != "stale_data_gate"


def test_stale_gate_bypassed_in_dev_mode(monkeypatch) -> None:
    monkeypatch.setattr(config, "INDIA_DEV_MODE", True)
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(last_tick_age_sec=None)
    assert chain.check(sig, ctx, SessionState.OPEN, _NOW) != "stale_data_gate"


def test_stale_gate_suppression_carries_telemetry() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(last_tick_age_sec=None)
    chain.check(sig, ctx, SessionState.OPEN, _NOW)
    supp = chain.suppressions[-1]
    assert supp.gate == "stale_data_gate"
    assert "no live tick" in supp.reason


# ── Feed: stall clock + restart ─────────────────────────────────────────


def _make_feed() -> FyersDataFeed:
    return FyersDataFeed(
        IndiaTickStore(), IndiaOIStore(), IndiaMarketData(), ExpiryManager()
    )


def test_seconds_since_last_tick_none_before_start() -> None:
    assert _make_feed().seconds_since_last_tick() is None


def test_tick_resets_stall_clock() -> None:
    feed = _make_feed()
    feed._last_tick_mono = time_mod.monotonic() - 500.0
    feed._process_tick(
        {"symbol": _SYM, "ltp": 24000.0, "vol_traded_today": 100.0}
    )
    age = feed.seconds_since_last_tick()
    assert age is not None and age < 1.0


def test_ignored_tick_does_not_reset_stall_clock() -> None:
    feed = _make_feed()
    feed._last_tick_mono = time_mod.monotonic() - 500.0
    feed._process_tick({"symbol": "", "ltp": 0.0})
    age = feed.seconds_since_last_tick()
    assert age is not None and age > 499.0


async def test_restart_uses_freshest_token(monkeypatch) -> None:
    from src.broker import token_store

    feed = _make_feed()
    feed._client_id = "APP-100"
    feed._access_token = "boot-token"
    monkeypatch.setattr(token_store, "load_token", lambda: "fresh-token")
    feed.stop = AsyncMock()  # type: ignore[method-assign]
    feed.start = AsyncMock()  # type: ignore[method-assign]

    await feed.restart()

    feed.stop.assert_awaited_once()
    feed.start.assert_awaited_once_with("APP-100", "fresh-token")


async def test_restart_falls_back_to_boot_token(monkeypatch) -> None:
    from src.broker import token_store

    feed = _make_feed()
    feed._client_id = "APP-100"
    feed._access_token = "boot-token"
    monkeypatch.setattr(token_store, "load_token", lambda: None)
    feed.stop = AsyncMock()  # type: ignore[method-assign]
    feed.start = AsyncMock()  # type: ignore[method-assign]

    await feed.restart()

    feed.start.assert_awaited_once_with("APP-100", "boot-token")


# ── Feed: subscribe only once the socket is really open ─────────────────


def test_subscribe_waits_for_open_socket(monkeypatch) -> None:
    """The SDK fires on_connect even when auth failed / the socket is not
    open, and wipes its outbound queue when the socket opens late — the
    subscription must be issued only against a genuinely open socket."""
    from src.broker import fyers_feed as ff

    monkeypatch.setattr(ff, "_SUBSCRIBE_POLL_SEC", 0.01)
    feed = _make_feed()
    feed._running = True
    feed._ws_symbols = [_SYM]

    ws = MagicMock()
    # Not connected on the first two polls, open on the third.
    ws.is_connected.side_effect = [False, False, True]
    feed._ws = ws

    feed._on_ws_connect()
    deadline = time_mod.monotonic() + 5.0
    while not ws.subscribe.called and time_mod.monotonic() < deadline:
        time_mod.sleep(0.01)

    ws.subscribe.assert_called_once_with(
        symbols=[_SYM], data_type="SymbolUpdate"
    )


def test_subscribe_gives_up_when_socket_never_opens(monkeypatch) -> None:
    from src.broker import fyers_feed as ff

    monkeypatch.setattr(ff, "_SUBSCRIBE_POLL_SEC", 0.01)
    monkeypatch.setattr(ff, "_SUBSCRIBE_WAIT_SEC", 0.05)
    feed = _make_feed()
    feed._running = True
    feed._ws_symbols = [_SYM]

    ws = MagicMock()
    ws.is_connected.return_value = False
    feed._ws = ws

    feed._on_ws_connect()
    time_mod.sleep(0.3)

    ws.subscribe.assert_not_called()
