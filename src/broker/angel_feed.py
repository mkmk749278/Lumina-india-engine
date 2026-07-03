"""Angel One SmartAPI data feed — zero-touch daily auth via official TOTP login.

Drop-in alternative to ``FyersDataFeed`` selected with ``DATA_FEED=angel``.
Angel One's documented ``generateSession(clientcode, pin, totp)`` login is
fully programmatic — the TOTP *is* the SEBI-mandated second factor, so the
engine re-authenticates itself every trading morning with no human step
(SmartAPI sessions expire at midnight IST).

Data path:
  - Historical 5m candles: ``getCandleData`` (NFO futures)
  - Live ticks + volume + OI: ``SmartWebSocketV2`` SNAP_QUOTE mode
    (OI arrives on every tick — no REST OI polling needed)
  - India VIX: NSE token subscribed on the same socket

Hard limits: tokens/TOTP secret are never logged, never surfaced in errors.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from datetime import datetime, timedelta
from datetime import time as dt_time
from typing import Any

import config
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market.candle import Candle
from src.session.expiry_manager import ExpiryManager
from src.utils import get_logger

logger = get_logger("angel_feed")

_RELOGIN_TIME = config._safe_time("ANGEL_RELOGIN_TIME", dt_time(8, 40))
_HISTORY_HOURS = 6
_EXCHANGE_NFO = 2  # SmartWebSocketV2 exchangeType
_EXCHANGE_NSE = 1
_SNAP_QUOTE = 3

# Angel NFO futures trading symbols embed the expiry as DDMMMYY,
# e.g. ``NIFTY31JUL25FUT``. Parsed defensively (both date orders seen
# in the wild across Angel doc versions).
_FUT_RE = re.compile(r"^(?P<base>[A-Z]+?)(?P<date>\d{2}[A-Z]{3}\d{2})FUT$")


def _parse_fut_expiry(date_part: str) -> datetime | None:
    for fmt in ("%d%b%y", "%y%b%d"):
        try:
            return datetime.strptime(date_part.title(), fmt)
        except ValueError:
            continue
    return None


class AngelDataFeed:
    """Angel One SmartAPI feed with self-managed daily re-login."""

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

        self._smart: Any = None
        self._ws: Any = None
        self._ws_thread: threading.Thread | None = None
        self._relogin_task: asyncio.Task[None] | None = None
        self._running = False

        # base -> tradingsymbol (what the scanner keys on), and the
        # reverse lookups the WS thread needs.
        self._symbols: dict[str, str] = {}
        self._token_to_symbol: dict[str, str] = {}
        self._nfo_tokens: list[str] = []
        self._vix_token: str = ""

    # ── Credentials ─────────────────────────────────────────────────────

    @staticmethod
    def credentials() -> dict[str, str]:
        return {
            "api_key": os.environ.get("ANGEL_API_KEY", ""),
            "client_code": os.environ.get("ANGEL_CLIENT_CODE", ""),
            "pin": os.environ.get("ANGEL_PIN", ""),
            "totp_secret": os.environ.get("ANGEL_TOTP_SECRET", ""),
        }

    @classmethod
    def has_credentials(cls) -> bool:
        return all(cls.credentials().values())

    @property
    def symbols(self) -> dict[str, str]:
        return dict(self._symbols)

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Login, resolve tokens, seed history, start WebSocket + re-login task."""
        self._running = True
        await asyncio.to_thread(self._login)
        await asyncio.to_thread(self._resolve_tokens)
        await asyncio.to_thread(self._seed_historical)
        self._start_websocket()
        self._relogin_task = asyncio.create_task(self._relogin_loop())

    async def stop(self) -> None:
        self._running = False
        if self._relogin_task and not self._relogin_task.done():
            self._relogin_task.cancel()
        self._close_ws()
        logger.info("angel data feed stopped")

    # ── Auth ────────────────────────────────────────────────────────────

    def _login(self) -> None:
        """Blocking SDK login — run via to_thread."""
        import pyotp
        from SmartApi.smartConnect import SmartConnect

        creds = self.credentials()
        self._smart = SmartConnect(api_key=creds["api_key"])
        totp = pyotp.TOTP(creds["totp_secret"]).now()
        result = self._smart.generateSession(creds["client_code"], creds["pin"], totp)

        if not isinstance(result, dict) or not result.get("status"):
            message = "unknown"
            if isinstance(result, dict):
                message = str(result.get("message", result.get("errorcode", "unknown")))
            raise ConnectionError(f"Angel login failed: {message}")

        name = result.get("data", {}).get("name", "unknown")
        logger.info("Angel session established for {}", name)

    def _session_tokens(self) -> tuple[str, str]:
        """(auth_token, feed_token) for the WebSocket, from the live session."""
        return (
            f"Bearer {self._smart.access_token}",
            str(self._smart.feed_token),
        )

    async def _relogin_loop(self) -> None:
        """Re-authenticate every morning — sessions die at midnight IST."""
        while self._running:
            now = datetime.now(config.IST)
            target = now.replace(
                hour=_RELOGIN_TIME.hour,
                minute=_RELOGIN_TIME.minute,
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
            try:
                await asyncio.sleep((target - now).total_seconds())
            except asyncio.CancelledError:
                return
            if not self._running:
                return
            try:
                logger.info("angel morning re-login starting")
                self._close_ws()
                await asyncio.to_thread(self._login)
                await asyncio.to_thread(self._resolve_tokens)
                await asyncio.to_thread(self._seed_historical)
                self._start_websocket()
                logger.info("angel morning re-login complete — feed live")
            except Exception:
                logger.opt(exception=True).error("angel morning re-login failed")

    # ── Symbol/token resolution ─────────────────────────────────────────

    def _resolve_tokens(self) -> None:
        """Resolve near-expiry futures tokens + VIX token via searchScrip."""
        symbols: dict[str, str] = {}
        token_map: dict[str, str] = {}
        nfo_tokens: list[str] = []
        today = datetime.now(config.IST).date()

        for base in config.ALLOWED_BASES:
            result = self._smart.searchScrip("NFO", base)
            rows = (result or {}).get("data") or []
            best: tuple[datetime, str, str] | None = None
            for row in rows:
                ts = str(row.get("tradingsymbol", ""))
                m = _FUT_RE.match(ts)
                if not m or m.group("base") != base:
                    continue
                expiry = _parse_fut_expiry(m.group("date"))
                if expiry is None or expiry.date() < today:
                    continue
                if best is None or expiry < best[0]:
                    best = (expiry, ts, str(row.get("symboltoken", "")))
            if best is None:
                raise ConnectionError(f"no active {base} future found via searchScrip")
            _, tradingsymbol, token = best
            symbols[base] = tradingsymbol
            token_map[token] = tradingsymbol
            nfo_tokens.append(token)
            logger.info("resolved {} -> {} (token {})", base, tradingsymbol, token)

        vix_token = ""
        try:
            result = self._smart.searchScrip("NSE", "INDIA VIX")
            for row in (result or {}).get("data") or []:
                if "VIX" in str(row.get("tradingsymbol", "")).upper():
                    vix_token = str(row.get("symboltoken", ""))
                    break
        except Exception:
            logger.opt(exception=True).warning("VIX token lookup failed")
        if not vix_token:
            logger.warning("India VIX token not resolved — VIX gate runs on defaults")

        self._symbols = symbols
        self._token_to_symbol = token_map
        self._nfo_tokens = nfo_tokens
        self._vix_token = vix_token

    # ── Historical seed ─────────────────────────────────────────────────

    def _seed_historical(self) -> None:
        now = datetime.now(config.IST)
        frm = (now - timedelta(hours=_HISTORY_HOURS)).strftime("%Y-%m-%d %H:%M")
        to = now.strftime("%Y-%m-%d %H:%M")

        for base, tradingsymbol in self._symbols.items():
            token = next(
                (t for t, s in self._token_to_symbol.items() if s == tradingsymbol),
                "",
            )
            try:
                resp = self._smart.getCandleData(
                    {
                        "exchange": "NFO",
                        "symboltoken": token,
                        "interval": "FIVE_MINUTE",
                        "fromdate": frm,
                        "todate": to,
                    }
                )
                rows = (resp or {}).get("data") or []
                candles = [
                    Candle(
                        ts=datetime.fromisoformat(str(r[0])),
                        open=float(r[1]),
                        high=float(r[2]),
                        low=float(r[3]),
                        close=float(r[4]),
                        volume=float(r[5]),
                    )
                    for r in rows
                    if isinstance(r, (list, tuple)) and len(r) >= 6
                ]
                if candles:
                    self._tick.seed(tradingsymbol, candles)
                    logger.info("seeded {} with {} 5m candles", base, len(candles))
                else:
                    logger.warning(
                        "no candles for {}: {}", base, (resp or {}).get("message", "")
                    )
            except Exception:
                logger.opt(exception=True).warning("historical seed failed for {}", base)

    # ── WebSocket ───────────────────────────────────────────────────────

    def _start_websocket(self) -> None:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2

        creds = self.credentials()
        auth_token, feed_token = self._session_tokens()

        ws = SmartWebSocketV2(
            auth_token=auth_token,
            api_key=creds["api_key"],
            client_code=creds["client_code"],
            feed_token=feed_token,
            max_retry_attempt=5,
        )

        token_list = [{"exchangeType": _EXCHANGE_NFO, "tokens": self._nfo_tokens}]
        if self._vix_token:
            token_list.append(
                {"exchangeType": _EXCHANGE_NSE, "tokens": [self._vix_token]}
            )

        def on_open(wsapp: Any) -> None:
            ws.subscribe("lumin-india", _SNAP_QUOTE, token_list)
            logger.info("angel WebSocket connected, subscribed {}", token_list)

        def on_data(wsapp: Any, message: Any) -> None:
            self._process_tick(message)

        def on_error(wsapp: Any, error: Any = None) -> None:
            logger.warning("angel WebSocket error: {}", error)

        def on_close(wsapp: Any) -> None:
            logger.warning("angel WebSocket closed")

        ws.on_open = on_open
        ws.on_data = on_data
        ws.on_error = on_error
        ws.on_close = on_close

        self._ws = ws
        self._ws_thread = threading.Thread(target=ws.connect, daemon=True)
        self._ws_thread.start()

    def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close_connection()
            except Exception:
                pass
            self._ws = None

    def _process_tick(self, tick: Any) -> None:
        """SNAP_QUOTE message → stores. Prices arrive as int paise."""
        if not self._running or not isinstance(tick, dict):
            return

        token = str(tick.get("token", ""))
        ltp_raw = tick.get("last_traded_price", 0) or 0
        ltp = float(ltp_raw) / 100.0
        if ltp <= 0:
            return

        ts_ms = tick.get("exchange_timestamp", 0) or 0
        ts = (
            datetime.fromtimestamp(ts_ms / 1000.0, tz=config.IST)
            if ts_ms > 0
            else datetime.now(config.IST)
        )

        if token == self._vix_token and self._vix_token:
            self._mkt.update_vix(ltp)
            return

        symbol = self._token_to_symbol.get(token)
        if symbol is None:
            return

        volume = float(tick.get("volume_trade_for_the_day", 0) or 0)
        self._tick.on_tick(symbol, ltp, volume, ts)

        oi = float(tick.get("open_interest", 0) or 0)
        if oi > 0:
            self._oi.update_oi(symbol, oi, ts)
