"""Angel One feed — symbol resolution, tick processing, credential gating."""

from __future__ import annotations

from datetime import datetime

import pytest

import config
from src.broker.angel_feed import _FUT_RE, AngelDataFeed, _parse_fut_expiry
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.session.expiry_manager import ExpiryManager


def _feed() -> AngelDataFeed:
    return AngelDataFeed(
        IndiaTickStore(), IndiaOIStore(), IndiaMarketData(), ExpiryManager()
    )


# ── Futures symbol parsing ────────────────────────────────────────────


def test_fut_regex_matches_index_future() -> None:
    m = _FUT_RE.match("NIFTY31JUL25FUT")
    assert m is not None
    assert m.group("base") == "NIFTY"
    assert m.group("date") == "31JUL25"


def test_fut_regex_excludes_niftynxt50() -> None:
    # Digits inside NXT50 break the [A-Z]+ base group, so NXT50 contracts
    # never match at all — NIFTY cannot pick them up.
    assert _FUT_RE.match("NIFTYNXT5031JUL25FUT") is None


def test_fut_regex_rejects_options() -> None:
    assert _FUT_RE.match("NIFTY31JUL2524500CE") is None


def test_parse_expiry_ddmmmyy() -> None:
    dt = _parse_fut_expiry("31JUL25")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2025, 7, 31)


def test_parse_expiry_reversed_order_tolerated() -> None:
    dt = _parse_fut_expiry("25JUL31")
    assert dt is not None
    assert dt.day in (25, 31)


def test_parse_expiry_garbage_is_none() -> None:
    assert _parse_fut_expiry("XXYYYZZ") is None


# ── Credentials gate ──────────────────────────────────────────────────


def test_has_credentials_requires_all_four(monkeypatch) -> None:
    for key in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert AngelDataFeed.has_credentials() is False

    monkeypatch.setenv("ANGEL_API_KEY", "k")
    monkeypatch.setenv("ANGEL_CLIENT_CODE", "C123")
    monkeypatch.setenv("ANGEL_PIN", "1234")
    assert AngelDataFeed.has_credentials() is False

    monkeypatch.setenv("ANGEL_TOTP_SECRET", "BASE32SECRET")
    assert AngelDataFeed.has_credentials() is True


# ── Tick processing (paise conversion, routing) ───────────────────────


@pytest.fixture
def live_feed() -> AngelDataFeed:
    feed = _feed()
    feed._running = True
    feed._symbols = {"NIFTY": "NIFTY31JUL25FUT"}
    feed._token_to_symbol = {"53001": "NIFTY31JUL25FUT"}
    feed._vix_token = "26017"
    return feed


def test_tick_converts_paise_and_feeds_stores(live_feed) -> None:
    live_feed._process_tick(
        {
            "token": "53001",
            "last_traded_price": 2450050,  # paise -> 24500.50
            "volume_trade_for_the_day": 1000,
            "open_interest": 5_000_000,
            "exchange_timestamp": int(
                datetime.now(config.IST).timestamp() * 1000
            ),
        }
    )
    candles = live_feed._tick.get_candles_5m("NIFTY31JUL25FUT")
    assert candles
    assert candles[-1].close == pytest.approx(24500.50)
    assert live_feed._oi.get_current_oi("NIFTY31JUL25FUT") == pytest.approx(5_000_000)


def test_vix_tick_routes_to_market_data(live_feed) -> None:
    live_feed._process_tick(
        {"token": "26017", "last_traded_price": 1420, "exchange_timestamp": 0}
    )
    assert live_feed._mkt.get_vix() == pytest.approx(14.20)


def test_unknown_token_ignored(live_feed) -> None:
    live_feed._process_tick(
        {"token": "99999", "last_traded_price": 100000, "exchange_timestamp": 0}
    )
    assert live_feed._tick.get_candles_5m("NIFTY31JUL25FUT") == []


def test_zero_price_ignored(live_feed) -> None:
    live_feed._process_tick({"token": "53001", "last_traded_price": 0})
    assert live_feed._tick.get_candles_5m("NIFTY31JUL25FUT") == []
