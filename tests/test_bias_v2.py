"""De-lagged direction classifier v2 (Session 21 — shadow only)."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.dependency import NEUTRAL, market_bias, market_bias_v2
from src.market.candle import Candle
from src.market_context import (
    MarketDirection,
    classify_market_direction_v2,
)
from src.regime import Regime
from src.signals.model import Direction, IndiaContext


def _candles(closes: list[float]) -> list[Candle]:
    base = config.IST.localize(datetime(2026, 7, 6, 9, 15))
    return [
        Candle(
            ts=base + timedelta(minutes=5 * i),
            open=c,
            high=c + 2,
            low=c - 2,
            close=c,
            volume=1000,
        )
        for i, c in enumerate(closes)
    ]


def _ctx(
    closes: list[float],
    *,
    day_open: float,
    vwap: float = 0.0,
    ema21: float = 0.0,
    prev_close: float = 0.0,
    regime_daily: Regime = Regime.RANGING,
) -> IndiaContext:
    return IndiaContext(
        base="NIFTY",
        regime_60m=Regime.RANGING,
        regime_daily=regime_daily,
        candles_5m=_candles(closes),
        volume_avg_5m_20=1000.0,
        atr14_5m=10.0,
        prev_day_high=0.0,
        prev_day_low=0.0,
        prev_day_close=prev_close,
        oi_change_15m_pct=0.0,
        india_vix=13.0,
        day_open=day_open,
        session_vwap=vwap,
        ema21_5m=ema21,
    )


def test_v2_labels_early_session_where_v1_is_blind() -> None:
    """Four bars into the day: v1 is structurally NEUTRAL (<21 bars); v2
    reads VWAP side + prev-close side + day change."""
    closes = [24400.0, 24440.0, 24480.0, 24520.0]  # +0.5% off the open
    ctx = _ctx(
        closes,
        day_open=24400.0,
        vwap=24450.0,  # price above VWAP
        prev_close=24380.0,  # price above prev close
    )
    assert market_bias(ctx) == NEUTRAL
    assert market_bias_v2(ctx) == Direction.LONG


def test_v2_flips_before_v1_on_reversal() -> None:
    """Late-day reversal: day change still positive (v1 stays LONG-ish) but
    price has crossed below VWAP and EMA21 — v2 reads SHORT."""
    closes = [24500.0 + i * 5 for i in range(20)] + [24540.0, 24500.0]
    ctx = _ctx(
        closes,
        day_open=24450.0,  # day still up ~0.2%
        vwap=24560.0,  # price below VWAP
        ema21=24555.0,  # price below EMA21
        prev_close=24400.0,
    )
    # v1: day change positive but price below EMA21 → NEUTRAL (latched off).
    assert market_bias(ctx) == NEUTRAL
    # v2: two of three votes SHORT (VWAP, EMA21) vs one LONG (day change).
    assert market_bias_v2(ctx) == Direction.SHORT


def test_v2_mixed_tape_stays_neutral() -> None:
    closes = [24500.0] * 22
    ctx = _ctx(
        closes,
        day_open=24500.0,  # flat day — no day-change vote
        vwap=24510.0,  # below VWAP → SHORT
        ema21=24490.0,  # above EMA21 → LONG
        prev_close=24500.0,
    )
    assert market_bias_v2(ctx) == NEUTRAL


def test_v2_market_direction_majority_rule() -> None:
    """One opposing vote no longer blocks the label (v1's zero-opposing rule
    is what made it latch only after the move was mature)."""
    up = _ctx(
        [24400.0 + i * 10 for i in range(22)],
        day_open=24400.0,
        vwap=24500.0,  # price 24610 above VWAP → LONG
        ema21=24550.0,  # above → LONG
        prev_close=24380.0,
        regime_daily=Regime.TRENDING_DOWN,  # one opposing daily vote
    )
    label = classify_market_direction_v2({"NIFTY": up, "BANKNIFTY": up})
    assert label == MarketDirection.LONG_BIASED


def test_v2_no_data_is_neutral() -> None:
    ctx = _ctx([], day_open=0.0)
    assert market_bias_v2(ctx) == NEUTRAL
    assert (
        classify_market_direction_v2({}) == MarketDirection.NEUTRAL
    )
