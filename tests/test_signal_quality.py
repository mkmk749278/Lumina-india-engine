"""Confidence scorer (spec §11) + tier assignment."""

from __future__ import annotations

import config
from src.regime import Regime
from src.signal_quality import IndiaSignalScoringEngine, tier_for
from src.signals.model import Direction, SetupClass, Tier
from tests.candle_factory import c
from tests.signal_factory import make_context, make_signal

ENGINE = IndiaSignalScoringEngine()


def test_tier_boundaries() -> None:
    assert tier_for(config.CONFIDENCE_A_PLUS) == Tier.A_PLUS
    assert tier_for(config.CONFIDENCE_A_PLUS - 0.1) == Tier.B
    # B spans [emit floor, A+); floor recalibrated 55 -> 50 (Session 10) for
    # post-#44 honest scores. Assert against config so the tune is single-source.
    assert tier_for(config.CONFIDENCE_EMIT_FLOOR) == Tier.B
    assert tier_for(config.CONFIDENCE_EMIT_FLOOR - 0.1) == Tier.FILTERED


def test_score_is_clamped_0_100() -> None:
    score = ENGINE.score(make_signal(), make_context())
    assert 0.0 <= score <= 100.0


def test_strong_aligned_signal_is_a_plus() -> None:
    # High-volume last bar, aligned regime, HTF confirmed, rich confluence, OI+.
    candles = [
        c(high=23998.5, low=23997.5, close=23998.0, volume=1000.0),
        c(high=23999.5, low=23998.5, close=23999.0, volume=1000.0),
        c(high=24000.5, low=23999.5, close=24000.0, volume=3000.0),
    ]
    signal = make_signal(
        direction=Direction.LONG,
        setup_class=SetupClass.TREND_PULLBACK_EMA,
        entry=24000.0,
        rr_ratio=3.0,
        htf_trend_aligned=True,
    )
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=candles,
        atr14_5m=10.0,
        prev_day_high=24000.0,       # confluence 1
        opening_range_high=24000.0,  # confluence 2 (round 24000 = confluence 3)
        oi_change_15m_pct=1.0,       # rising OI + rising price -> LONG confirm
    )
    score = ENGINE.score(signal, ctx)
    assert score >= 80.0
    assert tier_for(score) == Tier.A_PLUS


def test_weak_opposing_signal_is_filtered() -> None:
    candles = [
        c(high=24002.0, low=24001.0, close=24001.5, volume=500.0),
        c(high=24001.0, low=24000.0, close=24000.5, volume=500.0),
        c(high=24000.5, low=23999.0, close=24000.0, volume=500.0),
    ]
    signal = make_signal(
        direction=Direction.LONG,
        setup_class=SetupClass.TREND_PULLBACK_EMA,
        entry=24037.0,   # far from any key level
        rr_ratio=1.5,
    )
    ctx = make_context(
        regime_60m=Regime.TRENDING_DOWN,   # opposing -> 0 for TREND_PULLBACK
        regime_daily=Regime.TRENDING_DOWN,  # daily opposes LONG -> 4
        candles_5m=candles,
        volume_avg_5m_20=1000.0,            # ratio 0.5 -> 5
        oi_change_15m_pct=-1.0,             # unwinding -> 3
    )
    score = ENGINE.score(signal, ctx)
    assert score < 65.0
    assert tier_for(score) == Tier.FILTERED


def test_regime_affinity_aligned_beats_opposing() -> None:
    signal = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA, direction=Direction.LONG)
    aligned = ENGINE._score_regime(signal, make_context(regime_60m=Regime.TRENDING_UP))
    opposing = ENGINE._score_regime(signal, make_context(regime_60m=Regime.TRENDING_DOWN))
    assert aligned == 20.0
    assert opposing == 0.0


def test_pcr_contrarian_bonus() -> None:
    long_signal = make_signal(direction=Direction.LONG, setup_class=SetupClass.PCR_EXTREME)
    with_bonus = ENGINE._score_vix_pcr(long_signal, make_context(pcr_is_extreme_bearish=True))
    without = ENGINE._score_vix_pcr(long_signal, make_context())
    assert with_bonus == 8.0
    assert without == 5.0
