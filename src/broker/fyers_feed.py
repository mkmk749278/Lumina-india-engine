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
import random
import threading
import time as time_mod
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx

import config
from src.broker import token_store
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
_HTF_RESOLUTION = "15"
_DAILY_RESOLUTION = "D"
# ~300 calendar days -> ~200 daily bars; classify() needs >= 56.
_DAILY_FETCH_DAYS = 300
# Higher-timeframe seed: aggregating 60m off the 360h 5m window yields ~70
# bars — barely past EMA55's 56-bar minimum, leaving the regime EMAs (the
# largest scoring component's input) ~a third weighted toward their seed
# value. A dedicated 15m fetch reaches ~38 trading days (~950 bars, under
# the ~1000-candle response cap) and aggregates into ~230 clock-aligned 60m
# bars — properly converged. (Fyers' native "60" resolution is 09:15-session
# -aligned and would misalign with the tick store's clock-hour live bars, so
# we fetch 15m and aggregate.) One extra REST call per base per seed.
_HTF_FETCH_DAYS = 55
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
# Bases seeding in flight at once. Sequential seeding of 46 bases × 3 REST
# calls was a 1-2 minute feed-down window on every boot/refresh/hot-swap/
# watchdog restart; 5-way concurrency cuts it ~5x while staying polite to
# Fyers' per-second rate limits.
_SEED_CONCURRENCY = config._safe_int("FYERS_SEED_CONCURRENCY", 5)
# 429 backoff: Fyers rate-limits the burst of history calls at session open
# (46 bases × 5m + 15m + daily fetches). Without retry a throttled base is left
# unseeded for the whole day — it can't fire clean signals until live ticks
# rebuild its buffers. Retry on 429 with exponential backoff + jitter, honoring
# Retry-After, so the seed converges instead of dropping bases.
_HISTORY_MAX_RETRIES = config._safe_int("FYERS_HISTORY_MAX_RETRIES", 4)
_HISTORY_RETRY_BASE_SEC = config._safe_float("FYERS_HISTORY_RETRY_BASE_SEC", 0.5)
_HISTORY_RETRY_MAX_SEC = config._safe_float("FYERS_HISTORY_RETRY_MAX_SEC", 8.0)

_REDACTED = "***REDACTED***"

# How long _on_ws_connect waits for the socket to actually open before giving
# up on subscribing (the SDK fires on_connect even when auth failed — see
# _on_ws_connect).
_SUBSCRIBE_WAIT_SEC = 30.0
_SUBSCRIBE_POLL_SEC = 0.5


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
        on_daily_candles: Callable[[str, list[Candle]], None] | None = None,
    ) -> None:
        self._on_prev_day = on_prev_day
        self._on_daily_regime = on_daily_regime
        self._on_daily_candles = on_daily_candles
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
        # Monotonic clock of the last tick that reached _process_tick (any
        # symbol, VIX included — it measures "is the socket delivering",
        # not per-symbol liveness). Reset at start() so a fresh (re)start
        # gets a grace window before the watchdog can judge it stalled.
        self._last_tick_mono: float | None = None

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
        self._last_tick_mono = time_mod.monotonic()  # grace until first tick

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
        await self._seed_lot_sizes()
        self._start_websocket()
        self._oi_task = asyncio.create_task(self._poll_oi_loop())

    async def stop(self) -> None:
        """Clean shutdown."""
        self._running = False
        if self._oi_task and not self._oi_task.done():
            self._oi_task.cancel()
        if self._ws is not None:
            ws = self._ws
            try:
                # close_connection() joins the SDK's socket/message/ping
                # threads — a hard multi-second block if run on the event
                # loop (every hot-swap and watchdog restart goes through
                # here), so it runs in a worker thread.
                await asyncio.to_thread(ws.close_connection)
            except Exception:
                pass
        if self._http:
            await self._http.aclose()
        logger.info("data feed stopped")

    def seconds_since_last_tick(self) -> float | None:
        """Seconds since any tick reached the store (None before first
        start). The feed watchdog reads this: a running feed that has not
        delivered a tick for FEED_STALL_RESTART_SEC during market hours is
        dead regardless of what the SDK claims."""
        if self._last_tick_mono is None:
            return None
        return max(0.0, time_mod.monotonic() - self._last_tick_mono)

    async def restart(self) -> None:
        """Full stop + start with the freshest known credentials.

        Watchdog path: heals every silent WebSocket death mode (abandoned
        SDK reconnects, auth failure on connect, lost subscription) and the
        full reseed also repairs the candle gap the stall left behind.
        Prefers a token delivered via /fyers/callback over the one this
        feed was started with — the owner may have re-tapped while the
        feed was down.
        """
        client_id = self._client_id
        access_token = token_store.load_token() or self._access_token
        if not client_id or not access_token:
            raise RuntimeError("no Fyers credentials available for restart")
        await self.stop()
        await self.start(client_id, access_token)

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
        await self._seed_lot_sizes()

    # ── REST: lot-size resolution (broker symbol master) ────────────────

    async def _seed_lot_sizes(self) -> None:
        """Resolve NSE F&O lot sizes from the Fyers symbol master (once/day).

        Stock bases carry no static lot size, so their cards showed "lot 0" and
        rupee P&L is impossible without it. The public symbol master lists the
        NSE-mandated lot per underlying; we parse it once at seed. Best-effort:
        on any failure the static INSTRUMENTS fallback stands (indices keep their
        lots; stocks stay 0 until the next successful fetch). Not a hot path —
        one fetch per day, off the scan/tick loop.
        """
        if self._http is None:
            return
        try:
            resp = await self._http.get(
                config.FYERS_SYMBOL_MASTER_URL, timeout=httpx.Timeout(60.0)
            )
            resp.raise_for_status()
            lots = self._parse_lot_sizes(resp.text)
            if lots:
                config.set_resolved_lot_sizes(lots)
                logger.info(
                    "resolved lot sizes for {} F&O underlyings from symbol master",
                    len(lots),
                )
            else:
                logger.warning("symbol master returned no parseable lot sizes")
        except httpx.HTTPError:
            logger.opt(exception=True).warning(
                "lot-size symbol master fetch failed — static fallback stands"
            )

    @staticmethod
    def _parse_lot_sizes(csv_text: str) -> dict[str, int]:
        """Extract ``{underlying_base: lot_size}`` for futures from the Fyers
        NSE_FO symbol master.

        Columns (0-indexed, no header): 3 = lot size, 9 = Fyers ticker,
        13 = underlying base. Every expiry of an underlying shares its lot size,
        so a plain last-wins dedup by base is correct.
        """
        out: dict[str, int] = {}
        for line in csv_text.splitlines():
            parts = line.split(",")
            if len(parts) < 14:
                continue
            if not parts[9].endswith("FUT"):
                continue
            base = parts[13].strip().upper()
            try:
                lot = int(float(parts[3]))
            except ValueError:
                continue
            if base and lot > 0:
                out[base] = lot
        return out

    # ── REST: historical seed ───────────────────────────────────────────

    async def _seed_historical(self, now: datetime) -> None:
        """Seed the tick store and derive previous-session levels.

        The fetch window reaches back far enough to contain the previous
        trading session across a weekend or single holiday;
        prev_session_levels buckets by date and picks the latest pre-today
        session, so PDH / PDL / prev-close reach the evaluators. Only the
        recent tail is seeded into the ring buffer to keep its 200-candle
        budget intact.

        Bases seed under bounded concurrency: sequentially, 46 bases ×
        3 REST calls each was a 1–2 minute feed-down window at every boot,
        daily refresh, token hot-swap and watchdog restart. The semaphore
        keeps the burst polite to Fyers' rate limits while cutting the
        window ~5×.
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

        sem = asyncio.Semaphore(_SEED_CONCURRENCY)

        async def _bounded(base: str, symbol: str) -> None:
            async with sem:
                await self._seed_one(
                    base, symbol, now, range_from, range_to,
                    ring_cutoff, cur_bucket,
                )

        await asyncio.gather(
            *(_bounded(b, s) for b, s in self._symbols.items())
        )

        # Live volume deltas re-baseline against the fresh seed.
        self._cum_vol.reset()

    @staticmethod
    def _retry_after_seconds(resp: httpx.Response) -> float | None:
        """Parse a Retry-After header (delta-seconds form) if present."""
        try:
            return max(0.0, float(resp.headers.get("retry-after", "")))
        except (TypeError, ValueError):
            return None

    async def _history_get(
        self, params: dict[str, str], *, what: str
    ) -> httpx.Response | None:
        """GET the history endpoint with 429-aware retry.

        Fyers throttles the burst of history calls at session open. Retry on
        429 with exponential backoff + jitter (honoring Retry-After), capped at
        ``_HISTORY_MAX_RETRIES``. Returns the response on any non-429 status
        (the caller still handles other errors); returns ``None`` once 429s are
        exhausted — a best-effort miss the caller treats as "unseeded", already
        logged, no traceback."""
        assert self._http is not None
        delay = _HISTORY_RETRY_BASE_SEC
        for attempt in range(1, _HISTORY_MAX_RETRIES + 1):
            resp = await self._http.get(_HISTORY_URL, params=params)
            if resp.status_code != 429:
                return resp
            if attempt == _HISTORY_MAX_RETRIES:
                logger.warning(
                    "history 429 for {} — exhausted {} retries, left unseeded",
                    what,
                    _HISTORY_MAX_RETRIES,
                )
                return None
            wait = self._retry_after_seconds(resp) or delay
            wait = min(wait, _HISTORY_RETRY_MAX_SEC) + random.uniform(0.0, 0.4)
            logger.info(
                "history 429 for {} — retry {}/{} in {:.1f}s",
                what,
                attempt,
                _HISTORY_MAX_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
            delay = min(delay * 2, _HISTORY_RETRY_MAX_SEC)
        return None

    async def _seed_one(
        self,
        base: str,
        symbol: str,
        now: datetime,
        range_from: int,
        range_to: int,
        ring_cutoff: datetime,
        cur_bucket: datetime,
    ) -> None:
        """Seed one base (5m ring + HTF buffers + intraday state + daily
        regime). Best-effort — a failure leaves that base unseeded and never
        disturbs its siblings."""
        assert self._http is not None
        try:
            resp = await self._history_get(
                {
                    "symbol": symbol,
                    "resolution": _HISTORY_RESOLUTION,
                    "date_format": "0",
                    "range_from": str(range_from),
                    "range_to": str(range_to),
                    "cont_flag": "1",
                },
                what=base,
            )
            if resp is None:
                return
            resp.raise_for_status()
            data = resp.json()

            if data.get("s") != "ok":
                logger.warning(
                    "historical fetch failed for {}: {}",
                    base,
                    data.get("message", "unknown"),
                )
                return

            candles = self._parse_history_candles(data.get("candles", []))
            if not candles:
                logger.warning("no candles returned for {}", base)
                return

            levels = prev_session_levels(candles, now.date())
            if levels and self._on_prev_day is not None:
                self._on_prev_day(symbol, *levels)
                logger.info(
                    "prev-day levels for {}: H={:.1f} L={:.1f} C={:.1f}",
                    base,
                    *levels,
                )

            completed = [c for c in candles if c.ts < cur_bucket]

            # 5m ring seeds the recent intraday tail. 15m/60m seed from a
            # dedicated ~38-trading-day 15m fetch so the 60m regime EMAs
            # (EMA55 needs ~150 bars to converge) run on real history —
            # aggregating off the 5m window yielded only ~70 bars. Falls
            # back to aggregating the 5m window if the fetch fails.
            recent = [c for c in completed if c.ts >= ring_cutoff] or completed
            htf_15m = await self._fetch_htf_15m(base, symbol, now)
            if htf_15m:
                candles_15m = htf_15m
                candles_60m = aggregate_candles(htf_15m, 60)
            else:
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

    async def _fetch_htf_15m(
        self, base: str, symbol: str, now: datetime
    ) -> list[Candle]:
        """Fetch ~38 trading days of 15m candles for the higher-timeframe seed.

        Best-effort: any failure returns [] and the caller falls back to
        aggregating from the 5m window (the pre-Session-17 behaviour). The
        still-forming 15m bucket is excluded — live ticks rebuild it.
        """
        if self._http is None:
            return []
        try:
            resp = await self._history_get(
                {
                    "symbol": symbol,
                    "resolution": _HTF_RESOLUTION,
                    "date_format": "0",
                    "range_from": str(
                        int((now - timedelta(days=_HTF_FETCH_DAYS)).timestamp())
                    ),
                    "range_to": str(int(now.timestamp())),
                    "cont_flag": "1",
                },
                what=f"{base} 15m",
            )
            if resp is None:
                return []
            resp.raise_for_status()
            data = resp.json()
            if data.get("s") != "ok":
                logger.warning(
                    "15m history fetch failed for {}: {}",
                    base,
                    data.get("message", "unknown"),
                )
                return []
            candles = self._parse_history_candles(data.get("candles", []))
            cur_bucket = now.replace(
                minute=(now.minute // 15) * 15, second=0, microsecond=0
            )
            return [c for c in candles if c.ts < cur_bucket]
        except httpx.HTTPError:
            logger.opt(exception=True).warning(
                "15m seed HTTP error for {} — falling back to aggregation",
                base,
            )
            return []

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
            resp = await self._history_get(
                {
                    "symbol": symbol,
                    "resolution": _DAILY_RESOLUTION,
                    "date_format": "0",
                    "range_from": str(
                        int((now - timedelta(days=_DAILY_FETCH_DAYS)).timestamp())
                    ),
                    "range_to": str(int(now.timestamp())),
                    "cont_flag": "1",
                },
                what=f"{base} daily",
            )
            if resp is None:
                return
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
            # Hand the series to the context builder too, so the (optional,
            # default-off) intraday daily-regime refresh can fold today's
            # running bar without another fetch.
            if self._on_daily_candles is not None:
                self._on_daily_candles(symbol, daily)
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

        # reconnect_retry: the SDK default is 5 — five failed attempts and the
        # socket is abandoned with a print() the engine never sees (that plus
        # the overnight token expiry is how the feed died silently on
        # 2026-07-10). 50 is the SDK's hard ceiling; the feed watchdog is the
        # real safety net beyond that.
        self._ws = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            reconnect_retry=50,
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
        """Subscribe once the socket is *actually* open.

        Two verified SDK quirks make subscribing here directly unsafe:
        ``connect()`` fires this callback even when token validation failed
        and no socket exists (subscribe() then silently no-ops), and when the
        socket opens later than ``connect()``'s fixed 2s wait, the SDK's
        ``__on_open`` wipes its outbound queue — destroying a subscription
        issued too early. So: wait (bounded) for a real open socket, then
        subscribe. If it never opens, say so loudly — the feed watchdog will
        restart the feed.
        """
        ws = self._ws
        if ws is None:
            return

        def _subscribe_when_open() -> None:
            deadline = time_mod.monotonic() + _SUBSCRIBE_WAIT_SEC
            while time_mod.monotonic() < deadline:
                if not self._running:
                    return
                if ws.is_connected():
                    logger.info(
                        "WebSocket open — subscribing to {} symbols",
                        len(self._ws_symbols),
                    )
                    ws.subscribe(
                        symbols=self._ws_symbols, data_type="SymbolUpdate"
                    )
                    return
                time_mod.sleep(_SUBSCRIBE_POLL_SEC)
            logger.error(
                "WebSocket never opened within {}s — NOT subscribed"
                " (token/auth failure?); feed watchdog will restart",
                _SUBSCRIBE_WAIT_SEC,
            )

        threading.Thread(
            target=_subscribe_when_open, name="fyers-subscribe", daemon=True
        ).start()

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
        """Parse a single Fyers tick (WebSocket thread) and hand it to the
        event loop for ingestion.

        Fyers SymbolUpdate fields:
          symbol, ltp, vol_traded_today, open_price, high_price, low_price,
          close_price, ch, chp, timestamp, ...

        Parsing stays on the SDK thread; all store *mutation* happens on the
        event loop via ``call_soon_threadsafe`` — the stores are plain dicts/
        deques with multi-step invariants (building-candle updates, the
        day-rollover reset) and were previously written from this thread
        while the scanner/API read them concurrently with no synchronisation.
        Single-writer discipline deletes that whole class of torn reads;
        at ~46 ticks/s the loop hop is noise.
        """
        symbol = tick.get("symbol", "")
        ltp = tick.get("ltp", 0.0)
        cum_volume = tick.get("vol_traded_today", 0.0)
        ts_epoch = tick.get("exch_feed_time", 0) or tick.get("timestamp", 0)

        if not symbol or ltp <= 0:
            return

        self._last_tick_mono = time_mod.monotonic()

        if ts_epoch > 0:
            ts = datetime.fromtimestamp(ts_epoch, tz=config.IST)
        else:
            ts = datetime.now(config.IST)

        loop = self._loop
        if loop is None or loop.is_closed():
            self._ingest_tick(symbol, ltp, cum_volume, ts)  # tests / no loop
            return
        try:
            loop.call_soon_threadsafe(
                self._ingest_tick, symbol, ltp, cum_volume, ts
            )
        except RuntimeError:
            pass  # loop shutting down — drop the tick

    def _ingest_tick(
        self, symbol: str, ltp: float, cum_volume: float, ts: datetime
    ) -> None:
        """Store mutation — runs on the event loop (single writer).

        ``vol_traded_today`` is the *cumulative* day volume — the store needs
        the per-tick increment, so it goes through the delta tracker first
        (feeding the raw running total inflated live-bar volume by orders of
        magnitude and made every volume gate pass trivially).
        """
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
        self._mkt.compute_and_set_oi_walls(
            base, strikes, call_oi_list, put_oi_list
        )
