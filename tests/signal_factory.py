"""Factories for IndiaSignal / IndiaContext fixtures."""

from __future__ import annotations

from datetime import time

from src.market.candle import Candle
from src.regime import Regime
from src.signals.model import Direction, IndiaContext, IndiaSignal, SetupClass
from tests.candle_factory import c


def make_signal(
    *,
    direction: str = Direction.LONG,
    setup_class: str = SetupClass.TREND_PULLBACK_EMA,
    entry: float = 24000.0,
    rr_ratio: float = 2.0,
    htf_trend_aligned: bool = False,
    breakout_volume_ratio: float = 0.0,
) -> IndiaSignal:
    sl = entry - 20.0 if direction == Direction.LONG else entry + 20.0
    tp1 = entry + 40.0 if direction == Direction.LONG else entry - 40.0
    return IndiaSignal(
        signal_id="test",
        symbol="NSE:NIFTY26JULFUT-FF",
        base="NIFTY",
        direction=direction,
        setup_class=setup_class,
        entry=entry,
        sl=sl,
        tp1=tp1,
        sl_pct=0.08,
        tp1_pct=0.16,
        rr_ratio=rr_ratio,
        lot_size=75,
        htf_trend_aligned=htf_trend_aligned,
        breakout_volume_ratio=breakout_volume_ratio,
    )


def make_context(
    *,
    base: str = "NIFTY",
    regime_60m: Regime = Regime.RANGING,
    regime_daily: Regime = Regime.RANGING,
    candles_5m: list[Candle] | None = None,
    candles_15m: list[Candle] | None = None,
    volume_avg_5m_20: float = 1000.0,
    atr14_5m: float = 10.0,
    prev_day_high: float = 24100.0,
    prev_day_low: float = 23900.0,
    prev_day_close: float = 24010.0,
    oi_change_15m_pct: float = 0.0,
    india_vix: float = 15.0,
    pcr_is_extreme_bearish: bool = False,
    pcr_is_extreme_bullish: bool = False,
    opening_range_high: float | None = None,
    opening_range_low: float | None = None,
    symbol: str = "NSE:NIFTY26JULFUT-FF",
    tick_size: float = 0.05,
    day_open: float = 0.0,
    intraday_high: float = 0.0,
    intraday_low: float = 0.0,
    candles_60m: list[Candle] | None = None,
    current_oi: float = 0.0,
    scan_time_ist: time | None = None,
    is_expiry_day: bool = False,
    max_pain_strike: float | None = None,
    volume_avg_15m_20: float = 0.0,
) -> IndiaContext:
    if candles_5m is None:
        candles_5m = [
            c(high=p + 0.5, low=p - 0.5, close=float(p)) for p in (23998, 23999, 24000)
        ]
    return IndiaContext(
        base=base,
        regime_60m=regime_60m,
        regime_daily=regime_daily,
        candles_5m=candles_5m,
        volume_avg_5m_20=volume_avg_5m_20,
        atr14_5m=atr14_5m,
        prev_day_high=prev_day_high,
        prev_day_low=prev_day_low,
        prev_day_close=prev_day_close,
        oi_change_15m_pct=oi_change_15m_pct,
        india_vix=india_vix,
        pcr_is_extreme_bearish=pcr_is_extreme_bearish,
        pcr_is_extreme_bullish=pcr_is_extreme_bullish,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        symbol=symbol,
        tick_size=tick_size,
        candles_15m=candles_15m if candles_15m is not None else [],
        day_open=day_open,
        intraday_high=intraday_high,
        intraday_low=intraday_low,
        candles_60m=candles_60m if candles_60m is not None else [],
        current_oi=current_oi,
        scan_time_ist=scan_time_ist,
        is_expiry_day=is_expiry_day,
        max_pain_strike=max_pain_strike,
        volume_avg_15m_20=volume_avg_15m_20,
    )
