"""FyersDataFeed — historical parsing, tick processing, OI polling."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

import config
from src.broker.fyers_feed import FyersDataFeed, _auth_header
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.session.expiry_manager import ExpiryManager

IST = config.IST
_BASE_DT = IST.localize(datetime(2026, 7, 7, 0, 0, 0))
_SYM = "NSE:NIFTY26JULFUT"
_BASE = "NIFTY"


def _ist(h: int, m: int) -> datetime:
    return _BASE_DT + timedelta(hours=h, minutes=m)


def _make_feed() -> FyersDataFeed:
    tick = IndiaTickStore()
    oi = IndiaOIStore()
    mkt = IndiaMarketData()
    expiry = ExpiryManager()
    return FyersDataFeed(tick, oi, mkt, expiry)


# ── Auth header ─────────────────────────────────────────────────────────


def test_auth_header_format() -> None:
    h = _auth_header("APP-100", "mytoken123")
    assert h == {"Authorization": "APP-100:mytoken123"}


# ── Historical candle parsing ───────────────────────────────────────────


def test_parse_history_candles_valid() -> None:
    raw = [
        [1720333500.0, 24000.0, 24010.0, 23990.0, 24005.0, 1500.0],
        [1720333800.0, 24005.0, 24015.0, 23995.0, 24010.0, 1200.0],
    ]
    candles = FyersDataFeed._parse_history_candles(raw)
    assert len(candles) == 2
    assert candles[0].open == 24000.0
    assert candles[0].high == 24010.0
    assert candles[0].low == 23990.0
    assert candles[0].close == 24005.0
    assert candles[0].volume == 1500.0
    assert candles[0].ts.tzinfo is not None


def test_parse_lot_sizes_from_symbol_master() -> None:
    # Real Fyers NSE_FO layout: col 3 = lot, col 9 = ticker, col 13 = underlying.
    csv = (
        "101126072861088,BANKNIFTY 28 Jul 26 FUT,11,30,0.2,,x,2026-07-07,"
        "1785232800,NSE:BANKNIFTY26JULFUT,10,11,61088,BANKNIFTY,26009,-1.0\n"
        "101126072861091,RELIANCE 28 Jul 26 FUT,11,500,0.05,,x,2026-07-07,"
        "1785232800,NSE:RELIANCE26JULFUT,10,11,61091,RELIANCE,26037,-1.0\n"
        # An option row (ticker not *FUT) is ignored.
        "999,NIFTY 28 Jul 26 24000 CE,14,65,0.05,,x,2026-07-07,"
        "1785232800,NSE:NIFTY26JUL24000CE,10,11,1,NIFTY,1,-1.0\n"
    )
    lots = FyersDataFeed._parse_lot_sizes(csv)
    assert lots == {"BANKNIFTY": 30, "RELIANCE": 500}


def test_parse_lot_sizes_skips_malformed_rows() -> None:
    assert FyersDataFeed._parse_lot_sizes("too,few,cols\n\n") == {}


def test_parse_history_candles_empty() -> None:
    assert FyersDataFeed._parse_history_candles([]) == []


def test_parse_history_candles_skips_short_rows() -> None:
    raw = [
        [1720333500.0, 24000.0, 24010.0],
        [1720333800.0, 24005.0, 24015.0, 23995.0, 24010.0, 1200.0],
    ]
    candles = FyersDataFeed._parse_history_candles(raw)
    assert len(candles) == 1


# ── Tick processing ─────────────────────────────────────────────────────


def test_process_tick_feeds_tick_store() -> None:
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}
    feed._running = True
    feed._loop = MagicMock()

    tick_data = {
        "symbol": _SYM,
        "ltp": 24100.5,
        "vol_traded_today": 50000.0,
        "exch_feed_time": int(_ist(10, 30).timestamp()),
    }

    feed._process_tick(tick_data)

    assert feed._tick.get_intraday_high(_SYM) == 24100.5


def test_process_tick_updates_vix() -> None:
    feed = _make_feed()
    feed._running = True
    feed._loop = MagicMock()

    tick_data = {
        "symbol": "NSE:INDIAVIX-INDEX",
        "ltp": 17.5,
        "vol_traded_today": 0.0,
        "exch_feed_time": int(_ist(10, 30).timestamp()),
    }

    feed._process_tick(tick_data)
    assert feed._mkt.get_vix() == 17.5


def test_process_tick_ignores_zero_ltp() -> None:
    feed = _make_feed()
    feed._running = True
    feed._loop = MagicMock()

    tick_data = {"symbol": _SYM, "ltp": 0.0, "vol_traded_today": 100.0}
    feed._process_tick(tick_data)


def test_process_tick_ignores_empty_symbol() -> None:
    feed = _make_feed()
    feed._running = True
    feed._loop = MagicMock()

    tick_data = {"symbol": "", "ltp": 24100.0, "vol_traded_today": 100.0}
    feed._process_tick(tick_data)


# ── OI from quotes ──────────────────────────────────────────────────────


async def test_fetch_oi_data_updates_store() -> None:
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "s": "ok",
        "d": [
            {
                "n": _SYM,
                "v": {
                    "symbol": _SYM,
                    "ltp": 24100.0,
                    "open_interest": 5_000_000.0,
                },
            },
            {
                "n": "NSE:INDIAVIX-INDEX",
                "v": {"lp": 13.5},
            },
        ],
    }
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    feed._http = mock_client

    await feed._fetch_oi_data()

    assert feed._oi.get_current_oi(_SYM) == 5_000_000.0
    # VIX rides along in the quotes batch (WS-independent fallback).
    assert feed._mkt.get_vix() == 13.5
    # Batched: one request covers all symbols + VIX, not one per symbol.
    assert mock_client.get.await_count == 1


async def test_fetch_oi_data_skips_on_error() -> None:
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    feed._http = mock_client

    await feed._fetch_oi_data()
    assert feed._oi.get_current_oi(_SYM) == 0.0


# ── Option chain processing ────────────────────────────────────────────


def test_process_option_chain_updates_pcr() -> None:
    feed = _make_feed()

    chain_data = {
        "oc": [
            {
                "strike_price": 24000.0,
                "ce": {"oi": 500_000.0},
                "pe": {"oi": 400_000.0},
            },
            {
                "strike_price": 24100.0,
                "ce": {"oi": 300_000.0},
                "pe": {"oi": 600_000.0},
            },
        ]
    }

    feed._process_option_chain(_BASE, chain_data)

    assert not feed._oi.is_pcr_extreme_bearish()
    assert not feed._oi.is_pcr_extreme_bullish()


def test_process_option_chain_computes_max_pain() -> None:
    feed = _make_feed()

    chain_data = {
        "oc": [
            {
                "strike_price": 24000.0,
                "ce": {"oi": 1_000_000.0},
                "pe": {"oi": 200_000.0},
            },
            {
                "strike_price": 24100.0,
                "ce": {"oi": 200_000.0},
                "pe": {"oi": 1_000_000.0},
            },
        ]
    }

    feed._process_option_chain(_BASE, chain_data)
    mp = feed._mkt.get_max_pain(_BASE)
    assert mp > 0


def test_process_option_chain_empty() -> None:
    feed = _make_feed()
    feed._process_option_chain(_BASE, {"oc": []})


# ── Symbol resolution ───────────────────────────────────────────────────


def test_resolve_symbols() -> None:
    feed = _make_feed()
    symbols = feed._resolve_symbols(_ist(11, 0))
    assert "NIFTY" in symbols
    assert "BANKNIFTY" in symbols
    for sym in symbols.values():
        assert sym.startswith("NSE:")
        assert sym.endswith("FUT")


# ── Historical seed with mocked HTTP ────────────────────────────────────


async def test_seed_historical_populates_tick_store() -> None:
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}

    now = _ist(10, 0)
    epoch = int(now.timestamp())
    candle_data = [
        [epoch - 600, 24000.0, 24010.0, 23990.0, 24005.0, 1500.0],
        [epoch - 300, 24005.0, 24015.0, 23995.0, 24010.0, 1200.0],
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = {"s": "ok", "candles": candle_data}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    feed._http = mock_client

    await feed._seed_historical(now)

    candles = feed._tick.get_candles_5m(_SYM)
    assert len(candles) == 2


async def test_seed_historical_seeds_higher_timeframes() -> None:
    """The seed must populate 15m/60m buffers from the full history so the
    higher-timeframe regime can form at session open."""
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}

    now = _ist(12, 0)
    epoch = int(now.timestamp())
    # 24 consecutive 5m candles -> eight 15m bars, two 60m bars.
    candle_data = [
        [
            epoch - (24 - i) * 300,
            24000.0 + i, 24010.0 + i, 23990.0 + i, 24005.0 + i, 1000.0,
        ]
        for i in range(24)
    ]

    # Resolution-aware mock: 5m data for the ring seed; the dedicated 15m
    # HTF fetch fails so the seed exercises the aggregation fallback this
    # test asserts.
    async def _get(url, params=None, **kw):  # type: ignore[no-untyped-def]
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if params and params.get("resolution") == "5":
            resp.json.return_value = {"s": "ok", "candles": candle_data}
        else:
            resp.json.return_value = {"s": "no_data", "candles": []}
        return resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_get)
    feed._http = mock_client

    await feed._seed_historical(now)

    assert len(feed._tick.get_candles_15m(_SYM)) == 8
    assert len(feed._tick.get_candles_60m(_SYM)) == 2


async def test_seed_prefers_dedicated_15m_fetch_for_htf() -> None:
    """When the 15m-resolution fetch succeeds, the 15m ring holds its bars
    directly and 60m aggregates from them (converged regime EMAs, Session 17)
    instead of the short 5m-window aggregation."""
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}

    now = _ist(12, 0)
    epoch = int(now.timestamp())
    candle_5m = [
        [epoch - (24 - i) * 300, 24000.0, 24010.0, 23990.0, 24005.0, 1000.0]
        for i in range(24)
    ]
    # 48 consecutive 15m bars -> aggregates into 12 60m bars.
    candle_15m = [
        [epoch - (48 - i) * 900, 24000.0, 24010.0, 23990.0, 24005.0, 3000.0]
        for i in range(48)
    ]

    async def _get(url, params=None, **kw):  # type: ignore[no-untyped-def]
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        res = params.get("resolution") if params else None
        if res == "5":
            resp.json.return_value = {"s": "ok", "candles": candle_5m}
        elif res == "15":
            resp.json.return_value = {"s": "ok", "candles": candle_15m}
        else:
            resp.json.return_value = {"s": "no_data", "candles": []}
        return resp

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=_get)
    feed._http = mock_client

    await feed._seed_historical(now)

    assert len(feed._tick.get_candles_15m(_SYM)) >= 40
    assert len(feed._tick.get_candles_60m(_SYM)) >= 10


async def test_refresh_daily_reseeds_when_running() -> None:
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}
    feed._running = True

    now = _ist(9, 20)
    epoch = int(now.timestamp())
    candle_data = [[epoch - 300, 24000.0, 24010.0, 23990.0, 24005.0, 1000.0]]

    mock_response = MagicMock()
    mock_response.json.return_value = {"s": "ok", "candles": candle_data}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    feed._http = mock_client

    await feed.refresh_daily(now)
    assert len(feed._tick.get_candles_5m(_SYM)) == 1


async def test_refresh_daily_noop_when_not_connected() -> None:
    feed = _make_feed()  # not running, no http, no symbols
    await feed.refresh_daily(_ist(9, 15))  # must not raise


async def test_seed_historical_handles_api_error() -> None:
    feed = _make_feed()
    feed._symbols = {_BASE: _SYM}

    mock_response = MagicMock()
    mock_response.json.return_value = {"s": "error", "message": "invalid token"}
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    feed._http = mock_client

    now = _ist(10, 0)
    await feed._seed_historical(now)

    candles = feed._tick.get_candles_5m(_SYM)
    assert len(candles) == 0


# ── Start / stop lifecycle ──────────────────────────────────────────────


def test_start_websocket_connects_then_subscribes() -> None:
    """Regression: the feed must call connect() (which opens the socket and
    fires on_connect) and subscribe once the socket is genuinely open. The
    original bug called subscribe()/keep_running() but never connect(), so the
    socket never opened and zero live ticks ever arrived; the 2026-07-10
    follow-up defers the subscription until is_connected() is true (the SDK
    fires on_connect even on auth failure, and wipes its outbound queue when
    the socket opens late)."""
    import time as time_mod

    import fyers_apiv3.FyersWebsocket.data_ws as dw

    feed = _make_feed()
    feed._client_id = "APP-100"
    feed._access_token = "tok"
    feed._running = True  # start() sets this before _start_websocket()
    feed._symbols = {_BASE: _SYM, "BANKNIFTY": "NSE:BANKNIFTY26JULFUT"}

    state: dict = {"connected": False, "subscribed": None}

    class _FakeSocket:
        def __init__(self, **kwargs: object) -> None:
            self._cb = kwargs["on_connect"]

        def connect(self) -> None:
            state["connected"] = True
            self._cb()  # SDK invokes on_connect from connect()

        def is_connected(self) -> bool:
            return state["connected"]

        def subscribe(self, symbols: list, data_type: str = "SymbolUpdate") -> None:
            state["subscribed"] = (symbols, data_type)

    with patch.object(dw, "FyersDataSocket", _FakeSocket):
        feed._start_websocket()

    assert state["connected"] is True
    # The subscription is issued from a background waiter thread.
    deadline = time_mod.monotonic() + 5.0
    while state["subscribed"] is None and time_mod.monotonic() < deadline:
        time_mod.sleep(0.01)
    assert state["subscribed"] is not None
    symbols, data_type = state["subscribed"]
    assert set(symbols) == {_SYM, "NSE:BANKNIFTY26JULFUT", "NSE:INDIAVIX-INDEX"}
    assert data_type == "SymbolUpdate"


async def test_start_and_stop() -> None:
    feed = _make_feed()

    mock_response = MagicMock()
    mock_response.json.return_value = {"s": "ok", "candles": []}
    mock_response.raise_for_status = MagicMock()

    with (
        patch("httpx.AsyncClient") as mock_cls,
        patch(
            "src.broker.fyers_feed.FyersDataFeed._start_websocket"
        ) as mock_ws,
    ):
        mock_instance = AsyncMock()
        mock_instance.get = AsyncMock(return_value=mock_response)
        mock_instance.aclose = AsyncMock()
        mock_cls.return_value = mock_instance

        await feed.start("APP-100", "testtoken", now=_ist(10, 0))

        assert feed._running is True
        assert len(feed.symbols) > 0
        mock_ws.assert_called_once()

        await feed.stop()
        assert feed._running is False


# ── History fetch 429 backoff ───────────────────────────────────────────


def _resp(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("GET", "http://x")
    )


def test_retry_after_seconds_parses() -> None:
    assert FyersDataFeed._retry_after_seconds(_resp(429, {"retry-after": "5"})) == 5.0
    assert FyersDataFeed._retry_after_seconds(_resp(429)) is None


async def test_history_get_returns_non_429_immediately() -> None:
    feed = _make_feed()
    feed._http = MagicMock()
    feed._http.get = AsyncMock(return_value=_resp(200))
    resp = await feed._history_get({"symbol": "X"}, what="X")
    assert resp is not None and resp.status_code == 200
    assert feed._http.get.await_count == 1


async def test_history_get_retries_then_succeeds() -> None:
    feed = _make_feed()
    feed._http = MagicMock()
    feed._http.get = AsyncMock(
        side_effect=[_resp(429, {"retry-after": "0"}), _resp(200)]
    )
    with patch("src.broker.fyers_feed.asyncio.sleep", new=AsyncMock()) as slept:
        resp = await feed._history_get({"symbol": "X"}, what="X")
    assert resp is not None and resp.status_code == 200
    assert feed._http.get.await_count == 2
    slept.assert_awaited()


async def test_history_get_gives_up_after_max_retries(monkeypatch) -> None:
    monkeypatch.setattr("src.broker.fyers_feed._HISTORY_MAX_RETRIES", 3)
    feed = _make_feed()
    feed._http = MagicMock()
    feed._http.get = AsyncMock(return_value=_resp(429))
    with patch("src.broker.fyers_feed.asyncio.sleep", new=AsyncMock()):
        resp = await feed._history_get({"symbol": "X"}, what="X")
    assert resp is None
    assert feed._http.get.await_count == 3
