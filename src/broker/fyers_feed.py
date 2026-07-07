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
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

import config
from src.broker.history_utils import (
    CumulativeVolume,
    aggregate_candles,
    prev_session_levels,
)
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.regime import Regime, classify
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
# Option-chain poll cadence (PCR + max-pain). One REST call per index base.
_CHAIN_POLL_INTERVAL = config._safe_int("FYERS_CHAIN_POLL_SEC", 300)
# Fyers quotes endpoint accepts up to 50 comma-separated symbols per call.
_QUOTES_BATCH_SIZE = 50
_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"
_HISTORY_RESOLUTION = "5"
_DAILY_RESOLUTION = "D"
# ~300 calendar days -> ~200 daily bars; classify() needs >= 56.
_DAILY_FETCH_DAYS = 300
_MAX_CANDLES = 500

# Fyers spot-index symbols for option-chain requests. The futures symbol is
# not a valid option-chain underlier, and the underliers are NOT plain
# ``NSE:{base}-INDEX`` (NIFTY spot is ``NSE:NIFTY50-INDEX``).
_INDEX_CHAIN_SYMBOL: dict[str, str] = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "NIFTYNXT50": "NSE:NIFTYNEXT50-INDEX",
}
# The seed fetch must reach back far enough that the aggregated 60m series has
# >=56 bars (EMA21/EMA55 regime needs slow+1). ~11 trading days of market hours
# clears that (11 * 6.5h ~= 71 60m bars) and stays under Fyers' ~1000-candle
# response cap for 5m (~11 * 75 ~= 825 5m candles). Before this the 60m regime
# could not form for ~9 trading days after a (re)seed and sat permanently RANGING.
_FETCH_WINDOW_HOURS = 360
_RING_SEED_HOURS = 6

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
        on_prev_day: Callable[[str, float, float, float], None] | None = None,
        on_daily_regime: Callable[[str, Regime], None] | None = None,
    ) -> None:
        self._on_prev_day = on_prev_day
        self._on_daily_regime = on_daily_regime
        self._tick = tick_store
        self._oi = oi_store
        self._mkt = market_data
        self._expiry = expiry_mgr
        self._cum_vol = CumulativeVolume()

        self._client_id: str = ""
        self._access_token: str = ""
        self._http: httpx.AsyncClient | None = None
        self._ws: Any = None
        self._ws_symbols: list[str] = []
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

    async def refresh_daily(self, now: datetime | None = None) -> None:
        """Re-derive prev-day levels and re-seed candle buffers for a new day.

        Called at the session-open transition so a container that stays up
        across days does not keep serving stale prev-day levels (which produce
        nonsensical far targets) or a stale higher-timeframe regime. No-op if
        the feed never connected.
        """
        if not self._running or self._http is None or not self._symbols:
            return
        await self._seed_historical(now or datetime.now(config.IST))

    # ── REST: historical seed ───────────────────────────────────────────

    async def _seed_historical(self, now: datetime) -> None:
        """Seed the tick store and derive previous-session levels.

        The fetch window reaches back far enough (96h) to contain the
        previous trading session across a weekend or single holiday;
        prev_session_levels buckets by date and picks the latest pre-today
        session, so PDH / PDL / prev-close reach the evaluators. Only the
        recent tail is seeded into the ring buffer to keep its 200-candle
        budget intact.
        """
        assert self._http is not None

        range_to = int(now.timestamp())
        range_from = int((now - timedelta(hours=_FETCH_WINDOW_HOURS)).timestamp())
        ring_cutoff = now - timedelta(hours=_RING_SEED_HOURS)
        # The current 5m bucket is still forming — live ticks will rebuild it.
        # Seeding it as a completed bar would double it in the ring.
        cur_bucket = now.replace(
            minute=(now.minute // 5) * 5, second=0, microsecond=0
        )

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
                if not candles:
                    logger.warning("no candles returned for {}", base)
                    continue

                levels = prev_session_levels(candles, now.date())
                if levels and self._on_prev_day is not None:
                    self._on_prev_day(symbol, *levels)
                    logger.info(
                        "prev-day levels for {}: H={:.1f} L={:.1f} C={:.1f}",
                        base,
                        *levels,
                    )

                completed = [c for c in candles if c.ts < cur_bucket]

                # 5m ring seeds the recent intraday tail; 15m/60m seed from the
                # full window so the higher-timeframe regime has real history at
                # session open (aggregating 60m off the short 5m tail would only
                # yield a handful of bars).
                recent = [c for c in completed if c.ts >= ring_cutoff] or completed
                candles_15m = aggregate_candles(completed, 15)
                candles_60m = aggregate_candles(completed, 60)
                self._tick.seed(symbol, recent, candles_15m, candles_60m)

                # Rebuild today's day-open / opening range / intraday extremes
                # so a mid-session (re)start doesn't blind ORB/FAR and the
                # circuit gate for the rest of the day. The forming bucket is
                # fine to include here — extremes only extend.
                todays = [c for c in candles if c.ts.date() == now.date()]
                if todays:
                    self._tick.seed_intraday_state(symbol, todays, now)

                logger.info(
                    "seeded {} with {} 5m / {} 15m / {} 60m candles"
                    " ({} bars today)",
                    base,
                    len(recent),
                    len(candles_15m),
                    len(candles_60m),
                    len(todays),
                )

                await self._seed_daily_regime(base, symbol, now)

            except httpx.HTTPError:
                logger.opt(exception=True).warning(
                    "historical seed HTTP error for {}", base
                )

        # Live volume deltas re-baseline against the fresh seed.
        self._cum_vol.reset()

    async def _seed_daily_regime(
        self, base: str, symbol: str, now: datetime
    ) -> None:
        """Fetch daily candles and classify the daily-timeframe regime.

        ``regime_daily`` previously sat hardcoded RANGING — the HTF component
        of confidence scoring could never see a real daily trend. One REST
        call per base per seed (once a day) — nowhere near the hot path.
        """
        if self._on_daily_regime is None or self._http is None:
            return
        try:
            resp = await self._http.get(
                _HISTORY_URL,
                params={
                    "symbol": symbol,
                    "resolution": _DAILY_RESOLUTION,
                    "date_format": "0",
                    "range_from": str(
                        int((now - timedelta(days=_DAILY_FETCH_DAYS)).timestamp())
                    ),
                    "range_to": str(int(now.timestamp())),
                    "cont_flag": "1",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("s") != "ok":
                logger.warning(
                    "daily history fetch failed for {}: {}",
                    base,
                    data.get("message", "unknown"),
                )
                return
            daily = self._parse_history_candles(data.get("candles", []))
            # Exclude today's forming daily bar from the classification.
            daily = [c for c in daily if c.ts.date() < now.date()]
            regime = classify(daily) if len(daily) >= 56 else Regime.RANGING
            self._on_daily_regime(symbol, regime)
            logger.info(
                "daily regime for {}: {} ({} daily bars)",
                base,
                regime.value,
                len(daily),
            )
        except httpx.HTTPError:
            logger.opt(exception=True).warning(
                "daily regime fetch HTTP error for {}", base
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

        symbols_list = list(self._symbols.values())
        if _VIX_SYMBOL not in symbols_list:
            symbols_list.append(_VIX_SYMBOL)
        self._ws_symbols = symbols_list

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

        # connect() spawns the WebSocket's run_forever in a background thread
        # and then fires on_connect. Subscription must happen *after* the socket
        # is live, so it is issued from the on_connect callback below. The
        # previous code called subscribe()/keep_running() but never connect(),
        # so the socket never opened and not a single tick ever arrived — the
        # engine ran entirely on the static session-open seed.
        self._ws.connect()
        logger.info("WebSocket connecting, will subscribe to {}", symbols_list)

    def _on_ws_connect(self) -> None:
        logger.info("WebSocket connected — subscribing to {}", self._ws_symbols)
        if self._ws is not None:
            self._ws.subscribe(
                symbols=self._ws_symbols, data_type="SymbolUpdate"
            )

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

        ``vol_traded_today`` is the *cumulative* day volume — the store needs
        the per-tick increment, so it goes through the delta tracker first
        (feeding the raw running total inflated live-bar volume by orders of
        magnitude and made every volume gate pass trivially).
        """
        symbol = tick.get("symbol", "")
        ltp = tick.get("ltp", 0.0)
        cum_volume = tick.get("vol_traded_today", 0.0)
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

        volume = self._cum_vol.delta(symbol, cum_volume, ts)
        self._tick.on_tick(symbol, ltp, volume, ts)

    # ── REST: OI + option chain polling ─────────────────────────────────

    async def _poll_oi_loop(self) -> None:
        """Poll futures OI every ``_OI_POLL_INTERVAL`` seconds and the index
        option chains (PCR + max-pain) every ``_CHAIN_POLL_INTERVAL``."""
        last_chain_poll = 0.0
        while self._running:
            try:
                await self._fetch_oi_data()
                now_mono = asyncio.get_running_loop().time()
                if now_mono - last_chain_poll >= _CHAIN_POLL_INTERVAL:
                    last_chain_poll = now_mono
                    for base in self._symbols:
                        if base in _INDEX_CHAIN_SYMBOL:
                            await self.fetch_option_chain(base)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.opt(exception=True).warning("OI poll cycle failed")
            await asyncio.sleep(_OI_POLL_INTERVAL)

    async def _fetch_oi_data(self) -> None:
        """Fetch quotes (OI on futures + VIX fallback) in batched calls.

        One symbol per request was 46+ HTTP calls a minute after the universe
        expansion; the quotes endpoint takes up to 50 comma-separated symbols,
        so this is 1–2 calls. VIX rides along so the event-risk gate has a
        value even before the first WebSocket VIX tick after a (re)start.
        """
        assert self._http is not None

        symbols = list(self._symbols.values())
        if _VIX_SYMBOL not in symbols:
            symbols.append(_VIX_SYMBOL)

        now = datetime.now(config.IST)
        for i in range(0, len(symbols), _QUOTES_BATCH_SIZE):
            chunk = symbols[i : i + _QUOTES_BATCH_SIZE]
            try:
                resp = await self._http.get(
                    _QUOTES_URL,
                    params={"symbols": ",".join(chunk)},
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("s") != "ok":
                    continue

                for quote in data.get("d", []):
                    name = quote.get("n", "")
                    q = quote.get("v", {})
                    if name == _VIX_SYMBOL:
                        vix = q.get("lp", 0.0) or 0.0
                        if vix > 0:
                            self._mkt.update_vix(vix)
                        continue
                    oi = q.get("open_interest", 0.0)
                    if name and oi > 0:
                        self._oi.update_oi(name, oi, now)

            except httpx.HTTPError:
                logger.opt(exception=True).debug(
                    "quote batch fetch failed ({} symbols)", len(chunk)
                )

    async def fetch_option_chain(self, base: str) -> None:
        """Fetch the index option chain for PCR + max-pain updates.

        Called from the poll loop every ``_CHAIN_POLL_INTERVAL`` for each
        index base. Without a ``timestamp`` param Fyers returns the nearest
        expiry's chain — correct for weekly (NIFTY) and monthly-only
        (BANKNIFTY/FINNIFTY/NIFTYNXT50) cadences alike, so no expiry
        bookkeeping is needed here.
        """
        assert self._http is not None

        underlier = _INDEX_CHAIN_SYMBOL.get(base)
        if underlier is None:
            return

        try:
            resp = await self._http.get(
                _OPTION_CHAIN_URL,
                params={
                    "symbol": underlier,
                    "strikecount": "20",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("s") != "ok":
                logger.debug(
                    "option chain fetch for {} returned {}",
                    base,
                    data.get("message", data.get("s")),
                )
                return

            self._process_option_chain(base, data.get("data", {}))

        except httpx.HTTPError:
            logger.opt(exception=True).debug(
                "option chain fetch failed for {}", base
            )

    def _process_option_chain(
        self, base: str, chain_data: dict[str, Any]
    ) -> None:
        """Parse a Fyers v3 option chain for PCR and max-pain.

        The v3 response carries a flat ``optionsChain`` list (one row per
        strike per option_type CE/PE; the underlying rides along with
        ``strike_price`` -1) plus ``callOi``/``putOi`` chain totals. The
        legacy nested ``oc`` shape is kept as a fallback.
        """
        call_by_strike: dict[float, float] = {}
        put_by_strike: dict[float, float] = {}

        rows = chain_data.get("optionsChain", [])
        if rows:
            for row in rows:
                strike = float(row.get("strike_price", 0.0) or 0.0)
                if strike <= 0:
                    continue
                opt_type = str(row.get("option_type", "")).upper()
                oi = float(row.get("oi", 0.0) or 0.0)
                if opt_type == "CE":
                    call_by_strike[strike] = call_by_strike.get(strike, 0.0) + oi
                elif opt_type == "PE":
                    put_by_strike[strike] = put_by_strike.get(strike, 0.0) + oi
        else:
            for entry in chain_data.get("oc", []):
                strike = float(entry.get("strike_price", 0.0) or 0.0)
                if strike <= 0:
                    continue
                call_by_strike[strike] = float(
                    (entry.get("ce") or {}).get("oi", 0.0) or 0.0
                )
                put_by_strike[strike] = float(
                    (entry.get("pe") or {}).get("oi", 0.0) or 0.0
                )

        strikes = sorted(set(call_by_strike) | set(put_by_strike))
        if not strikes:
            return
        call_oi_list = [call_by_strike.get(s, 0.0) for s in strikes]
        put_oi_list = [put_by_strike.get(s, 0.0) for s in strikes]

        # Prefer the chain-total fields when present (whole chain, not just
        # the fetched strike window).
        total_call_oi = float(chain_data.get("callOi", 0.0) or 0.0) or sum(
            call_oi_list
        )
        total_put_oi = float(chain_data.get("putOi", 0.0) or 0.0) or sum(
            put_oi_list
        )

        if total_call_oi > 0 and total_put_oi > 0:
            self._oi.update_pcr(total_put_oi, total_call_oi, base)

        self._mkt.compute_and_set_max_pain(
            base, strikes, call_oi_list, put_oi_list
        )
