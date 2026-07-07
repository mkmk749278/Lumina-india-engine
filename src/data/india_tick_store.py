"""In-memory ring-buffer candle store, fed by ticks.

Ticks arrive from the Fyers WebSocket during market hours.  This store
aggregates them into 5m / 15m / 60m candles, maintains a ring buffer per
(symbol, timeframe), and tracks intraday state the scanner reads:

  - Opening Range high / low (09:15–09:45 IST, locked at 09:45)
  - Day open, intraday high / low
  - Volume averages, ATR

The store is seeded at session open with historical candles from the Fyers
REST API (via ``seed``).  After that, ``on_tick`` builds live candles.

CLAUDE.md cost discipline: this store is entirely in-memory — zero I/O on the
hot path (per-tick, per-scan).
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime, time

from config import MARKET_OPEN
from src.indicators import atr as compute_atr
from src.indicators import rolling_mean
from src.market.candle import Candle, volumes

_OR_END = time(9, 45)

_TF_MINUTES = {"5m": 5, "15m": 15, "60m": 60}


def _bar_open_time(ts: datetime, tf_minutes: int) -> datetime:
    """Truncate *ts* to the start of its timeframe bucket."""
    total = ts.hour * 60 + ts.minute
    bucket_start = (total // tf_minutes) * tf_minutes
    h, m = divmod(bucket_start, 60)
    return ts.replace(hour=h, minute=m, second=0, microsecond=0)


class _BuildingCandle:
    """Mutable candle accumulator for the current (incomplete) bar."""

    __slots__ = ("ts", "open", "high", "low", "close", "volume", "tick_count")

    def __init__(self, ts: datetime, price: float, volume: float) -> None:
        self.ts = ts
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = volume
        self.tick_count = 1

    def update(self, price: float, volume: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1

    def freeze(self) -> Candle:
        return Candle(
            ts=self.ts,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class IndiaTickStore:
    """Per-symbol, multi-timeframe candle ring buffer."""

    def __init__(self, max_candles: int = 200) -> None:
        self._max = max_candles

        self._candles: dict[str, dict[str, deque[Candle]]] = {}
        self._building: dict[str, dict[str, _BuildingCandle | None]] = {}

        self._day_open: dict[str, float] = {}
        self._intraday_high: dict[str, float] = {}
        self._intraday_low: dict[str, float] = {}

        self._or_high: dict[str, float] = {}
        self._or_low: dict[str, float] = {}
        self._or_locked: set[str] = set()

        # IST date the current intraday state belongs to. The first tick of a
        # new trading day auto-clears day_open / opening range / intraday
        # extremes so they never carry over from a prior session (the store is
        # a long-lived, process-scoped object — a stale day_open silently trips
        # the scanner's circuit gate and freezes the opening range for every
        # evaluator downstream).
        self._state_date: date | None = None

    # ------------------------------------------------------------------
    # Init
    # ------------------------------------------------------------------

    def _ensure_symbol(self, symbol: str) -> None:
        if symbol not in self._candles:
            self._candles[symbol] = {
                tf: deque(maxlen=self._max) for tf in _TF_MINUTES
            }
            self._building[symbol] = {tf: None for tf in _TF_MINUTES}

    # ------------------------------------------------------------------
    # Seed (called at session open with historical candles)
    # ------------------------------------------------------------------

    def seed(
        self,
        symbol: str,
        candles_5m: list[Candle],
        candles_15m: list[Candle] | None = None,
        candles_60m: list[Candle] | None = None,
    ) -> None:
        """Load historical candles into the ring buffers.

        Only 5m is required.  15m and 60m are optional — if not supplied
        they are aggregated from the 5m series.
        """
        self._ensure_symbol(symbol)
        buf = self._candles[symbol]

        buf["5m"].clear()
        buf["5m"].extend(candles_5m[-self._max :])

        if candles_15m is not None:
            buf["15m"].clear()
            buf["15m"].extend(candles_15m[-self._max :])
        else:
            buf["15m"].clear()
            buf["15m"].extend(
                self._aggregate(candles_5m, 15)[-self._max :]
            )

        if candles_60m is not None:
            buf["60m"].clear()
            buf["60m"].extend(candles_60m[-self._max :])
        else:
            buf["60m"].clear()
            buf["60m"].extend(
                self._aggregate(candles_5m, 60)[-self._max :]
            )

    # ------------------------------------------------------------------
    # Tick ingestion
    # ------------------------------------------------------------------

    def on_tick(
        self,
        symbol: str,
        price: float,
        volume: float,
        ts: datetime,
    ) -> None:
        """Process one tick (price + incremental volume + IST-aware timestamp).

        The Fyers WebSocket client computes volume deltas before calling this.
        """
        self._ensure_symbol(symbol)

        tick_date = ts.date()
        if self._state_date is None:
            self._state_date = tick_date
        elif tick_date != self._state_date:
            # New trading day — drop yesterday's intraday state before this
            # tick seeds today's day_open / opening range. Candle ring buffers
            # are preserved (reset_day keeps them) for indicator continuity.
            self.reset_day()
            self._state_date = tick_date

        if symbol not in self._day_open:
            self._day_open[symbol] = price
            self._intraday_high[symbol] = price
            self._intraday_low[symbol] = price
        else:
            self._intraday_high[symbol] = max(
                self._intraday_high[symbol], price
            )
            self._intraday_low[symbol] = min(
                self._intraday_low[symbol], price
            )

        t = ts.timetz()
        if not self._is_or_locked(symbol) and t >= MARKET_OPEN and t < _OR_END:
            if symbol not in self._or_high:
                self._or_high[symbol] = price
                self._or_low[symbol] = price
            else:
                self._or_high[symbol] = max(self._or_high[symbol], price)
                self._or_low[symbol] = min(self._or_low[symbol], price)
        elif t >= _OR_END:
            self._or_locked.add(symbol)

        for tf, tf_min in _TF_MINUTES.items():
            bar_ts = _bar_open_time(ts, tf_min)
            building = self._building[symbol][tf]

            if building is None:
                self._building[symbol][tf] = _BuildingCandle(
                    bar_ts, price, volume
                )
            elif bar_ts > building.ts:
                self._candles[symbol][tf].append(building.freeze())
                self._building[symbol][tf] = _BuildingCandle(
                    bar_ts, price, volume
                )
            else:
                building.update(price, volume)

    # ------------------------------------------------------------------
    # Readers (consumed by context builder / scanner)
    # ------------------------------------------------------------------

    def get_candles(
        self, symbol: str, tf: str, include_building: bool = True
    ) -> list[Candle]:
        """Return completed candles, optionally including the current bar."""
        self._ensure_symbol(symbol)
        result = list(self._candles[symbol].get(tf, []))
        if include_building:
            building = self._building.get(symbol, {}).get(tf)
            if building is not None and building.tick_count > 0:
                result.append(building.freeze())
        return result

    def get_candles_5m(
        self, symbol: str, include_building: bool = True
    ) -> list[Candle]:
        return self.get_candles(symbol, "5m", include_building)

    def get_candles_15m(
        self, symbol: str, include_building: bool = True
    ) -> list[Candle]:
        return self.get_candles(symbol, "15m", include_building)

    def get_candles_60m(
        self, symbol: str, include_building: bool = True
    ) -> list[Candle]:
        return self.get_candles(symbol, "60m", include_building)

    def get_volume_avg(
        self, symbol: str, tf: str, period: int = 20
    ) -> float:
        candles = self.get_candles(symbol, tf, include_building=False)
        vols = volumes(candles)
        if len(vols) < period:
            return rolling_mean(vols, len(vols)) if vols else 0.0
        return rolling_mean(vols, period)

    def get_atr14_5m(self, symbol: str) -> float:
        candles = self.get_candles(symbol, "5m", include_building=False)
        if len(candles) < 15:
            return 0.0
        return compute_atr(candles, 14)

    def get_opening_range(
        self, symbol: str
    ) -> tuple[float | None, float | None]:
        return (
            self._or_high.get(symbol),
            self._or_low.get(symbol),
        )

    def get_day_open(self, symbol: str) -> float:
        return self._day_open.get(symbol, 0.0)

    def get_intraday_high(self, symbol: str) -> float:
        return self._intraday_high.get(symbol, 0.0)

    def get_intraday_low(self, symbol: str) -> float:
        return self._intraday_low.get(symbol, 0.0)

    def has_data(self, symbol: str) -> bool:
        buf = self._candles.get(symbol, {})
        return len(buf.get("5m", [])) >= 1

    # ------------------------------------------------------------------
    # Day reset (called at session close)
    # ------------------------------------------------------------------

    def reset_day(self) -> None:
        """Clear intraday state.  Ring buffers are preserved for next-day seed."""
        self._day_open.clear()
        self._intraday_high.clear()
        self._intraday_low.clear()
        self._or_high.clear()
        self._or_low.clear()
        self._or_locked.clear()
        for symbol in self._building:
            for tf in self._building[symbol]:
                self._building[symbol][tf] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_or_locked(self, symbol: str) -> bool:
        return symbol in self._or_locked

    @staticmethod
    def _aggregate(candles_5m: list[Candle], tf_minutes: int) -> list[Candle]:
        """Aggregate 5m candles into a higher timeframe."""
        if not candles_5m:
            return []
        bars_per_htf = tf_minutes // 5
        result: list[Candle] = []
        i = 0
        while i + bars_per_htf <= len(candles_5m):
            group = candles_5m[i : i + bars_per_htf]
            result.append(
                Candle(
                    ts=group[0].ts,
                    open=group[0].open,
                    high=max(c.high for c in group),
                    low=min(c.low for c in group),
                    close=group[-1].close,
                    volume=sum(c.volume for c in group),
                )
            )
            i += bars_per_htf
        return result
