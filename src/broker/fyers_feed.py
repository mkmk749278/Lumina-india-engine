"""Fyers API v3 data feed — WebSocket ticks + REST historical/OI/VIX.

Feeds the in-memory data stores (IndiaTickStore, IndiaOIStore,
IndiaMarketData) with live NSE market data during market hours.

Phase 1: data access only (ticks, candles, OI, VIX). No order placement.

Architecture:
  - ``httpx.AsyncClient`` for REST (historical candles, option chain, VIX)
  - ``fyers-apiv3`` ``FyersDataSocket`` for WebSocket ticks (runs in its
    own thread; callbacks bridge to the async event loop)

CLAUDE.md cost discipline: the WebSocket is free (included in Fyers API
access). REST polling is 1 call/min for OI — negligible. Historical seed
is once at session open.

Hard limits enforced here:
  - Access token is NEVER logged, written to disk, or surfaced in errors.
  - Token is held in memory only, passed via env var at container start.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx

import config
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.session.expiry_manager import ExpiryManager
from src.utils import get_logger

logger = get_logger("fyers_feed")

# Fyers v3 splits its REST surface: account/auth endpoints live under
# /api/v3/, market-data endpoints under /data/ (verified on the wire —
# /api/v3/history is a hard 404, /data/history authenticates).
_DATA_BASE = "https://api-t1.fyers.in/data"
_HISTORY_URL = f"{_DATA_BASE}/history"
_QUOTES_URL = f"{_DATA_BASE}/quotes"
_OPTION_CHAIN_URL = f"{_DATA_BASE}/options-chain-v3"

_OI_POLL_INTERVAL = config._safe_int("FYERS_OI_POLL_SEC", 60)
_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
_HISTORY_RESOLUTION = "5"
_MAX_CANDLES = 500

_REDACTED = "***REDACTED***"


def _auth_header(client_id: str, access_token: str) -> dict[str, str]:
    return {"Authorization": f"{client_id}:{access_token}"}


class FyersDataFeed:
    """Manages Fyers WebSocket + REST data feeds for Phase 1.

    Lifecycle:
      1. ``start()`` — seed historical candles, start WebSocket + polling
      2. Engine runs scan loop reading from the data stores
      3. ``stop()`` — clean shutdown at session end
    """

    def __init__(
        self,
        tick_store: IndiaTickStore,
        oi_store: IndiaOIStore,
        market_data: IndiaMarketData,
        expiry_mgr: ExpiryManager,
    ) -> None:
        self._tick = tick_store
        self._oi = oi_store
        self._mkt = market_data
        self._expiry = expiry_mgr

        self._client_id: str = ""
        self._access_token: str = ""
        self._http: httpx.AsyncClient | None = None
        self._ws: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._oi_task: asyncio.Task[None] | None = None
        self._running = False
        self._symbols: dict[str, str] = {}

    @property
    def symbols(self) -> dict[str, str]:
        return dict(self._symbols)

    async def start(
        self, client_id: str, access_token: str, now: datetime | None = None
    ) -> None:
        """Initialize feeds. Call once at session open."""
        self._client_id = client_id
        self._access_token = access_token
        self._loop = asyncio.get_running_loop()
        self._running = True

        self._http = httpx.AsyncClient(
            headers=_auth_header(client_id, access_token),
            timeout=httpx.Timeout(15.0),
        )

        now = now or datetime.now(config.IST)
        self._symbols = self._resolve_symbols(now)
        logger.info(
            "starting data feed for {} symbols: {}",
            len(self._symbols),
            list(self._symbols.keys()),
        )

        await self._seed_historical(now)
        self._start_websocket()
        self._oi_task = asyncio.create_task(self._poll_oi_loop())

    async def stop(self) -> None:
        """Clean shutdown."""
        self._running = False
        if self._oi_task and not self._oi_task.done():
            self._oi_task.cancel()
        if self._ws is not None:
            try:
                self._ws.close_connection()
            except Exception:
                pass
        if self._http:
            await self._http.aclose()
        logger.info("data feed stopped")

    def _resolve_symbols(self, now: datetime) -> dict[str, str]:
        """Build {base: fyers_symbol} for all allowed bases."""
        symbols: dict[str, str] = {}
        for base in config.ALLOWED_BASES:
            try:
                symbols[base] = self._expiry.get_active_symbol(base, now)
            except ValueError:
                logger.warning("skipping disallowed base {}", base)
        return symbols

    # ── REST: historical seed ───────────────────────────────────────────

    async def _seed_historical(self, now: datetime) -> None:
        """Fetch recent 5m candles from Fyers REST API and seed tick store."""
        assert self._http is not None

        range_to = int(now.timestamp())
        range_from = int((now - timedelta(hours=6)).timestamp())

        for base, symbol in self._symbols.items():
            try:
                resp = await self._http.get(
                    _HISTORY_URL,
                    params={
                        "symbol": symbol,
                        "resolution": _HISTORY_RESOLUTION,
                        "date_format": "0",
                        "range_from": str(range_from),
                        "range_to": str(range_to),
                        "cont_flag": "1",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("s") != "ok":
                    logger.warning(
                        "historical fetch failed for {}: {}",
                        base,
                        data.get("message", "unknown"),
                    )
                    continue

                candles = self._parse_history_candles(data.get("candles", []))
                if candles:
                    self._tick.seed(symbol, candles)
                    logger.info(
                        "seeded {} with {} 5m candles", base, len(candles)
                    )
                else:
                    logger.warning("no candles returned for {}", base)

            except httpx.HTTPError:
                logger.opt(exception=True).warning(
                    "historical seed HTTP error for {}", base
                )

    @staticmethod
    def _parse_history_candles(raw: list[list[float]]) -> list[Candle]:
        """Parse Fyers history response into Candle objects.

        Fyers format: ``[[epoch, open, high, low, close, volume], ...]``
        """
        candles: list[Candle] = []
        for row in raw:
            if len(row) < 6:
                continue
            ts = datetime.fromtimestamp(row[0], tz=config.IST)
            candles.append(
                Candle(
                    ts=ts,
                    open=row[1],
                    high=row[2],
                    low=row[3],
                    close=row[4],
                    volume=row[5],
                )
            )
        return candles

    # ── WebSocket: live ticks ───────────────────────────────────────────

    def _start_websocket(self) -> None:
        """Start Fyers data WebSocket in a background thread."""
        try:
            from fyers_apiv3.FyersWebsocket import data_ws
        except ImportError:
            logger.error(
                "fyers-apiv3 not installed — WebSocket feed unavailable. "
                "Install with: pip install fyers-apiv3"
            )
            return

        access_token = f"{self._client_id}:{self._access_token}"

        self._ws = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=self._on_ws_connect,
            on_close=self._on_ws_close,
            on_error=self._on_ws_error,
            on_message=self._on_ws_message,
        )

        symbols_list = list(self._symbols.values())
        if _VIX_SYMBOL not in symbols_list:
            symbols_list.append(_VIX_SYMBOL)

        self._ws.subscribe(symbols=symbols_list, data_type="SymbolUpdate")
        self._ws.keep_running()
        logger.info("WebSocket started, subscribed to {}", symbols_list)

    def _on_ws_connect(self) -> None:
        logger.info("WebSocket connected")

    def _on_ws_close(self) -> None:
        logger.warning("WebSocket closed")

    def _on_ws_error(self, error: Any) -> None:
        logger.warning("WebSocket error: {}", error)

    def _on_ws_message(self, message: Any) -> None:
        """Called on the WebSocket thread. Bridge data to the stores."""
        if not self._running or self._loop is None:
            return

        if isinstance(message, list):
            for item in message:
                self._process_tick(item)
        elif isinstance(message, dict):
            self._process_tick(message)

    def _process_tick(self, tick: dict[str, Any]) -> None:
        """Parse a single Fyers tick and feed stores.

        Fyers SymbolUpdate fields:
          symbol, ltp, vol_traded_today, open_price, high_price, low_price,
          close_price, ch, chp, timestamp, ...
        """
        symbol = tick.get("symbol", "")
        ltp = tick.get("ltp", 0.0)
        volume = tick.get("vol_traded_today", 0.0)
        ts_epoch = tick.get("exch_feed_time", 0) or tick.get("timestamp", 0)

        if not symbol or ltp <= 0:
            return

        if ts_epoch > 0:
            ts = datetime.fromtimestamp(ts_epoch, tz=config.IST)
        else:
            ts = datetime.now(config.IST)

        if symbol == _VIX_SYMBOL:
            self._mkt.update_vix(ltp)
            return

        self._tick.on_tick(symbol, ltp, volume, ts)

    # ── REST: OI + option chain polling ─────────────────────────────────

    async def _poll_oi_loop(self) -> None:
        """Poll option chain every ``_OI_POLL_INTERVAL`` seconds."""
        while self._running:
            try:
                await self._fetch_oi_data()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.opt(exception=True).warning("OI poll cycle failed")
            await asyncio.sleep(_OI_POLL_INTERVAL)

    async def _fetch_oi_data(self) -> None:
        """Fetch quotes (for OI on futures) and update stores."""
        assert self._http is not None

        for base, symbol in self._symbols.items():
            try:
                resp = await self._http.get(
                    _QUOTES_URL,
                    params={"symbols": symbol},
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("s") != "ok":
                    continue

                quotes = data.get("d", [])
                if not quotes:
                    continue

                q = quotes[0].get("v", {})
                oi = q.get("open_interest", 0.0)
                if oi > 0:
                    now = datetime.now(config.IST)
                    self._oi.update_oi(symbol, oi, now)

            except httpx.HTTPError:
                logger.opt(exception=True).debug(
                    "quote fetch failed for {}", base
                )

    async def fetch_option_chain(self, base: str) -> None:
        """Fetch option chain for OI aggregation and max-pain computation.

        Call this periodically (e.g. every 5 min) for PCR and max-pain updates.
        """
        assert self._http is not None

        expiry = self._expiry.get_expiry_date()
        expiry_epoch = int(
            datetime.combine(expiry, datetime.min.time()).timestamp()
        )

        try:
            resp = await self._http.get(
                _OPTION_CHAIN_URL,
                params={
                    "symbol": f"NSE:{base}-INDEX",
                    "strikecount": "20",
                    "timestamp": str(expiry_epoch),
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("s") != "ok":
                return

            self._process_option_chain(base, data.get("data", {}))

        except httpx.HTTPError:
            logger.opt(exception=True).debug(
                "option chain fetch failed for {}", base
            )

    def _process_option_chain(
        self, base: str, chain_data: dict[str, Any]
    ) -> None:
        """Parse option chain for PCR and max-pain computation."""
        oc_list = chain_data.get("oc", [])
        if not oc_list:
            return

        total_call_oi = 0.0
        total_put_oi = 0.0
        strikes: list[float] = []
        call_oi_list: list[float] = []
        put_oi_list: list[float] = []

        for entry in oc_list:
            strike = entry.get("strike_price", 0.0)
            if strike <= 0:
                continue

            ce = entry.get("ce", {})
            pe = entry.get("pe", {})
            c_oi = ce.get("oi", 0.0)
            p_oi = pe.get("oi", 0.0)

            total_call_oi += c_oi
            total_put_oi += p_oi
            strikes.append(strike)
            call_oi_list.append(c_oi)
            put_oi_list.append(p_oi)

        if total_call_oi > 0 and total_put_oi > 0:
            self._oi.update_pcr(total_put_oi, total_call_oi)

        if strikes:
            self._mkt.compute_and_set_max_pain(
                base, strikes, call_oi_list, put_oi_list
            )
