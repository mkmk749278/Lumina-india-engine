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
    base: str = "NIFTY",
    entry: float = 24000.0,
    sl: float | None = None,
    tp1: float | None = None,
    rr_ratio: float = 2.0,
    htf_trend_aligned: bool = False,
    breakout_volume_ratio: float = 0.0,
) -> IndiaSignal:
    if sl is None:
        sl = entry - 20.0 if direction == Direction.LONG else entry + 20.0
    if tp1 is None:
        tp1 = entry + 40.0 if direction == Direction.LONG else entry - 40.0
    return IndiaSignal(
        signal_id="test",
        symbol="NSE:NIFTY26JULFUT",
        base=base,
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
    # A trending daily by default: the default setup is a trend-continuation
    # one (TREND_PULLBACK_EMA), and the regime/setup gate now suppresses that
    # family in a ranging/quiet daily. Tests that exercise the ranging-daily
    # path (chop / regime-setup gates) set this explicitly.
    regime_daily: Regime = Regime.TRENDING_UP,
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
    pcr: float = 0.0,
    opening_range_high: float | None = None,
    opening_range_low: float | None = None,
    symbol: str = "NSE:NIFTY26JULFUT",
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
    opening_range_locked: bool | None = None,
    # Fresh by default so setup-logic tests aren't suppressed; the
    # stale_data_gate tests pass None / a stale age explicitly.
    last_tick_age_sec: float | None = 0.0,
    # Complete bar by default so setup-logic tests aren't suppressed; the
    # pattern-bar-discipline tests pass a forming fraction explicitly.
    bar_elapsed_fraction: float = 1.0,
    key_levels_extra: list[float] | None = None,
    fii_dii_net_cr: float = 0.0,
) -> IndiaContext:
    # A test that supplies an opening range means it as a final level unless
    # it says otherwise (the pre-09:45 forming-range case sets this False).
    if opening_range_locked is None:
        opening_range_locked = opening_range_high is not None
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
        pcr=pcr,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        opening_range_locked=opening_range_locked,
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
        last_tick_age_sec=last_tick_age_sec,
        bar_elapsed_fraction=bar_elapsed_fraction,
        key_levels_extra=key_levels_extra if key_levels_extra is not None else [],
        fii_dii_net_cr=fii_dii_net_cr,
    )
