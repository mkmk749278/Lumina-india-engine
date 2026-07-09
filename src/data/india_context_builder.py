"""Assembles ``IndiaContext`` from the data stores for one scan cycle.

Called by the scanner once per (symbol, scan-tick).  Reads from the tick
store, OI store, and market data — all in-memory, zero I/O.
"""

from __future__ import annotations

from datetime import datetime

import config
from config import INSTRUMENTS, IST
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.market_profile import tod_adjusted_volume_ratio
from src.regime import Regime, classify
from src.session.expiry_manager import ExpiryManager
from src.signals.model import IndiaContext


class IndiaContextBuilder:
    """Stateless builder — takes stores, returns ``IndiaContext``."""

    def __init__(
        self,
        tick_store: IndiaTickStore,
        oi_store: IndiaOIStore,
        market_data: IndiaMarketData,
        expiry_mgr: ExpiryManager,
        prev_day_high: dict[str, float] | None = None,
        prev_day_low: dict[str, float] | None = None,
        prev_day_close: dict[str, float] | None = None,
    ) -> None:
        self._tick = tick_store
        self._oi = oi_store
        self._mkt = market_data
        self._expiry = expiry_mgr
        self._prev_high = prev_day_high or {}
        self._prev_low = prev_day_low or {}
        self._prev_close = prev_day_close or {}
        self._daily_regime: dict[str, Regime] = {}

    def set_prev_day(
        self, symbol: str, high: float, low: float, close: float
    ) -> None:
        self._prev_high[symbol] = high
        self._prev_low[symbol] = low
        self._prev_close[symbol] = close

    def set_daily_regime(self, symbol: str, regime: Regime) -> None:
        """Set the daily-timeframe regime, classified from the feed's daily
        history fetch at seed time (a daily regime does not move intraday)."""
        self._daily_regime[symbol] = regime

    def build(self, symbol: str, base: str, now: datetime) -> IndiaContext:
        """Build a context snapshot for *symbol* at time *now* (IST)."""
        inst = INSTRUMENTS.get(base)
        tick_size = inst.tick_size if inst else 0.05

        candles_5m = self._tick.get_candles_5m(symbol)
        candles_15m = self._tick.get_candles_15m(symbol)
        candles_60m = self._tick.get_candles_60m(symbol)

        atr14 = self._tick.get_atr14_5m(symbol)

        regime_60m = (
            classify(candles_60m) if len(candles_60m) >= 15 else Regime.RANGING
        )
        regime_daily = self._daily_regime.get(symbol, Regime.RANGING)

        or_high, or_low = self._tick.get_opening_range(symbol)

        ist_now = now.astimezone(IST) if now.tzinfo else IST.localize(now)
        scan_time = ist_now.timetz()

        # IB16 expiry-day behaviour: index bases key off the weekly (Tuesday)
        # options expiry; stock F&O has no weekly cadence, so stock bases key
        # off their monthly contract expiry day.
        if base in config.INDEX_BASES:
            is_expiry = self._expiry.is_weekly_expiry_day(ist_now)
        else:
            is_expiry = self._expiry.is_contract_expiry_day(ist_now)

        return IndiaContext(
            base=base,
            symbol=symbol,
            tick_size=tick_size,
            regime_60m=regime_60m,
            regime_daily=regime_daily,
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            candles_60m=candles_60m,
            volume_avg_5m_20=self._tick.get_volume_avg(symbol, "5m", 20),
            volume_avg_15m_20=self._tick.get_volume_avg(symbol, "15m", 20),
            volume_ratio_tod=tod_adjusted_volume_ratio(candles_5m, scan_time),
            atr14_5m=atr14,
            prev_day_high=self._prev_high.get(symbol, 0.0),
            prev_day_low=self._prev_low.get(symbol, 0.0),
            prev_day_close=self._prev_close.get(symbol, 0.0),
            oi_change_15m_pct=self._oi.get_oi_change_15m_pct(symbol),
            india_vix=self._mkt.get_vix(),
            pcr_is_extreme_bearish=self._oi.is_pcr_extreme_bearish(),
            pcr_is_extreme_bullish=self._oi.is_pcr_extreme_bullish(),
            opening_range_high=or_high,
            opening_range_low=or_low,
            day_open=self._tick.get_day_open(symbol),
            intraday_high=self._tick.get_intraday_high(symbol),
            intraday_low=self._tick.get_intraday_low(symbol),
            current_oi=self._oi.get_current_oi(symbol),
            scan_time_ist=scan_time,
            is_expiry_day=is_expiry,
            max_pain_strike=self._mkt.get_max_pain(base),
        )
