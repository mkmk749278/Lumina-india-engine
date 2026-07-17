"""Assembles ``IndiaContext`` from the data stores for one scan cycle.

Called by the scanner once per (symbol, scan-tick).  Reads from the tick
store, OI store, and market data — all in-memory, zero I/O.
"""

from __future__ import annotations

from datetime import datetime

import config
from config import INSTRUMENTS, IST
from src.data.india_macro_store import IndiaMacroStore
from src.data.india_market_data import IndiaMarketData
from src.data.india_oi_store import IndiaOIStore
from src.data.india_tick_store import IndiaTickStore
from src.indicators import ema
from src.market.candle import Candle
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
        macro_store: IndiaMacroStore | None = None,
    ) -> None:
        self._tick = tick_store
        self._oi = oi_store
        self._mkt = market_data
        self._expiry = expiry_mgr
        self._macro = macro_store
        self._prev_high = prev_day_high or {}
        self._prev_low = prev_day_low or {}
        self._prev_close = prev_day_close or {}
        self._daily_regime: dict[str, Regime] = {}
        # Seeded daily series (pre-today) per symbol — the substrate for the
        # optional intraday daily-regime refresh (B4 fix, default OFF).
        self._daily_candles: dict[str, list[Candle]] = {}

    def set_prev_day(
        self, symbol: str, high: float, low: float, close: float
    ) -> None:
        self._prev_high[symbol] = high
        self._prev_low[symbol] = low
        self._prev_close[symbol] = close

    def set_daily_regime(self, symbol: str, regime: Regime) -> None:
        """Set the daily-timeframe regime, classified from the feed's daily
        history fetch at seed time."""
        self._daily_regime[symbol] = regime

    def set_daily_candles(self, symbol: str, candles: list) -> None:
        """Retain the seeded daily series (pre-today) so the intraday
        daily-regime refresh can fold today's running bar without a fetch."""
        self._daily_candles[symbol] = list(candles)

    def refresh_daily_regime(self, symbol: str, now: datetime) -> Regime | None:
        """Re-classify the daily regime with today's RUNNING daily bar folded
        in (Session 21, B4 fix — default OFF via
        ``INDIA_DAILY_REGIME_REFRESH_MIN=0``).

        The seed-time label froze all session: a range-morning that develops
        into a trend afternoon (or vice versa) misdirected the chop /
        regime-setup gates for the whole day. Zero I/O — the running bar is
        assembled from the tick store's day state. Returns the new regime, or
        None when the inputs aren't available (label left unchanged)."""
        hist = self._daily_candles.get(symbol)
        if not hist:
            return None
        day_open = self._tick.get_day_open(symbol)
        hi = self._tick.get_intraday_high(symbol)
        lo = self._tick.get_intraday_low(symbol)
        last = self._tick.get_last_price(symbol)
        if min(day_open, hi, lo, last) <= 0:
            return None
        ist_now = now.astimezone(IST) if now.tzinfo else IST.localize(now)
        today_bar = Candle(
            ts=ist_now.replace(hour=0, minute=0, second=0, microsecond=0),
            open=day_open,
            high=hi,
            low=lo,
            close=last,
            volume=0.0,
        )
        regime = classify([*hist, today_bar])
        self._daily_regime[symbol] = regime
        return regime

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

        # Freshness of the newest live tick (None = seed-only data, no live
        # tick ever). Drives the stale_data_gate — a signal computed on a
        # frozen buffer has an entry nobody can fill.
        last_tick_ts = self._tick.get_last_tick_ts(symbol)
        last_tick_age = (
            max(0.0, (ist_now - last_tick_ts).total_seconds())
            if last_tick_ts is not None
            else None
        )

        # IB16 expiry-day behaviour. Weekly options exist only where SEBI's
        # one-weekly-per-exchange rule left them (NSE: NIFTY, Tuesdays) — every
        # other base, index or stock, expires with its monthly contract (last
        # Tuesday). Keying all index bases off the weekly flag gave BANKNIFTY/
        # FINNIFTY a false expiry-day confidence bump every Tuesday and armed
        # the gamma-squeeze path on days with no expiring options.
        if base in config.WEEKLY_OPTION_BASES:
            is_expiry = self._expiry.is_weekly_expiry_day(ist_now)
        else:
            is_expiry = self._expiry.is_contract_expiry_day(ist_now)

        # Elapsed fraction of the forming 5m bar (1.0 once complete) — the
        # pattern-triggered evaluators wait for a near-final bar.
        bar_fraction = 1.0
        if candles_5m:
            bar_age = (ist_now - candles_5m[-1].ts).total_seconds()
            if 0 <= bar_age < 300:
                bar_fraction = bar_age / 300.0

        # Session VWAP — the institutional intraday anchor. Fed to the scorer
        # through key_levels_extra so proximity to VWAP counts as level
        # confluence alongside OR/PDH/PDL/PDC/round numbers.
        extra_levels: list[float] = []
        session_vwap = self._session_vwap(candles_5m, ist_now)
        if session_vwap > 0:
            extra_levels.append(session_vwap)

        # EMA21 on 5m closes — the second extension anchor (0.0 until 21
        # bars exist). Pure in-memory arithmetic, no I/O.
        ema21_5m = 0.0
        if len(candles_5m) >= 21:
            try:
                ema21_5m = ema([c.close for c in candles_5m], 21)
            except ValueError:
                ema21_5m = 0.0

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
            pcr=self._oi.get_pcr(),
            opening_range_high=or_high,
            opening_range_low=or_low,
            opening_range_locked=self._tick.is_opening_range_locked(symbol),
            day_open=self._tick.get_day_open(symbol),
            intraday_high=self._tick.get_intraday_high(symbol),
            intraday_low=self._tick.get_intraday_low(symbol),
            current_oi=self._oi.get_current_oi(symbol),
            scan_time_ist=scan_time,
            is_expiry_day=is_expiry,
            max_pain_strike=self._mkt.get_max_pain(base),
            last_tick_age_sec=last_tick_age,
            bar_elapsed_fraction=bar_fraction,
            key_levels_extra=extra_levels,
            fii_dii_net_cr=self._macro.get_net_cr() if self._macro else 0.0,
            session_vwap=session_vwap,
            ema21_5m=ema21_5m,
        )

    @staticmethod
    def _session_vwap(candles_5m: list, ist_now: datetime) -> float:
        """Volume-weighted average price over *today's* 5m bars (0.0 when
        today has no bars or no volume yet)."""
        num = 0.0
        den = 0.0
        for c in candles_5m:
            if c.ts.date() != ist_now.date():
                continue
            num += (c.high + c.low + c.close) / 3.0 * c.volume
            den += c.volume
        return num / den if den > 0 else 0.0
