"""Confidence scorer (9-component rebudget) + tier assignment."""

from __future__ import annotations

import config
from src.regime import Regime
from src.signal_quality import IndiaSignalScoringEngine, tier_for
from src.signals.model import Direction, SetupClass, Tier
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context, make_signal

ENGINE = IndiaSignalScoringEngine()


def test_tier_boundaries() -> None:
    # Three tiers per IB14: A+ >= 80, A >= 65, B >= emit floor (50).
    assert tier_for(config.CONFIDENCE_A_PLUS) == Tier.A_PLUS
    assert tier_for(config.CONFIDENCE_A_PLUS - 0.1) == Tier.A
    assert tier_for(config.CONFIDENCE_A) == Tier.A
    assert tier_for(config.CONFIDENCE_A - 0.1) == Tier.B
    assert tier_for(config.CONFIDENCE_EMIT_FLOOR) == Tier.B
    assert tier_for(config.CONFIDENCE_EMIT_FLOOR - 0.1) == Tier.FILTERED


def test_score_is_clamped_0_100() -> None:
    score = ENGINE.score(make_signal(), make_context())
    assert 0.0 <= score <= 100.0


def test_strong_aligned_signal_is_a_plus() -> None:
    # High-volume last bar, aligned regime, HTF confirmed, rich confluence,
    # OI long-buildup, fat net-of-cost target.
    candles = [
        c(high=23998.5, low=23997.5, close=23998.0, volume=1000.0),
        c(high=23999.5, low=23998.5, close=23999.0, volume=1000.0),
        c(high=24000.5, low=23999.5, close=24000.0, volume=3000.0),
    ]
    signal = make_signal(
        direction=Direction.LONG,
        setup_class=SetupClass.TREND_PULLBACK_EMA,
        entry=24000.0,
        sl=24000.0 - 60.0,
        tp1=24000.0 + 150.0,
        rr_ratio=2.5,
        htf_trend_aligned=True,
    )
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=candles,
        atr14_5m=10.0,
        prev_day_high=24000.0,       # confluence 1
        opening_range_high=24000.0,  # confluence 2 (round 24000 = confluence 3)
        oi_change_15m_pct=1.0,       # rising OI + rising price -> long buildup
    )
    ctx.index_bias = Direction.LONG  # proxy index confirms
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
        regime_daily=Regime.TRENDING_DOWN,  # daily opposes LONG -> 3
        candles_5m=candles,
        volume_avg_5m_20=1000.0,            # ratio 0.5 -> 5
        oi_change_15m_pct=-1.0,             # long unwinding vs LONG -> 3
    )
    ctx.index_bias = Direction.SHORT        # proxy index opposes -> 0
    score = ENGINE.score(signal, ctx)
    assert score < config.CONFIDENCE_EMIT_FLOOR
    assert tier_for(score) == Tier.FILTERED


def test_regime_affinity_aligned_beats_opposing() -> None:
    signal = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA, direction=Direction.LONG)
    aligned = ENGINE._score_regime(signal, make_context(regime_60m=Regime.TRENDING_UP))
    opposing = ENGINE._score_regime(signal, make_context(regime_60m=Regime.TRENDING_DOWN))
    assert aligned == 15.0
    assert opposing == 0.0


def test_pcr_contrarian_bonus() -> None:
    long_signal = make_signal(direction=Direction.LONG, setup_class=SetupClass.PCR_EXTREME)
    with_bonus = ENGINE._score_vix_pcr(long_signal, make_context(pcr_is_extreme_bearish=True))
    without = ENGINE._score_vix_pcr(long_signal, make_context())
    assert with_bonus == 6.0
    assert without == 4.0


def test_pcr_wrong_side_penalised_both_directions() -> None:
    # Crowded bullish positioning is a headwind for a LONG, not just for
    # a SHORT facing bearish extremes.
    long_signal = make_signal(
        direction=Direction.LONG, setup_class=SetupClass.OPENING_RANGE_BREAKOUT
    )
    penalised = ENGINE._score_vix_pcr(long_signal, make_context(pcr_is_extreme_bullish=True))
    neutral = ENGINE._score_vix_pcr(long_signal, make_context())
    assert penalised < neutral


def test_rr_score_is_net_of_round_trip_cost() -> None:
    # Two NIFTY longs with the SAME gross 2:1 geometry but different absolute
    # size. After the ~14-pt round-trip cost the fat-target trade keeps far more
    # net edge, so it must out-score the thin scalp the gross ratio calls equal.
    thin = make_signal(entry=24000.0, sl=24000.0 - 10.0, tp1=24000.0 + 20.0)
    fat = make_signal(entry=24000.0, sl=24000.0 - 60.0, tp1=24000.0 + 120.0)
    assert ENGINE._score_rr(fat) > ENGINE._score_rr(thin)


def test_rr_score_floors_a_break_even_scalp() -> None:
    # TP1 a hair above the round-trip cost -> net reward ~0 -> lowest RR band.
    cost = config.round_trip_cost_points(24000.0)
    barely = make_signal(entry=24000.0, sl=24000.0 - 20.0, tp1=24000.0 + cost + 1.0)
    assert ENGINE._score_rr(barely) == 3.0


# ── OI positioning matrix ────────────────────────────────────────────


def _oi_ctx(*, closes: tuple[float, float, float], oi_chg: float):
    return make_context(
        candles_5m=[c(high=p + 0.5, low=p - 0.5, close=p) for p in closes],
        oi_change_15m_pct=oi_chg,
    )


def test_oi_long_buildup_backs_long_not_short() -> None:
    ctx = _oi_ctx(closes=(23990.0, 23995.0, 24000.0), oi_chg=1.0)
    long_score = ENGINE._score_oi(make_signal(direction=Direction.LONG), ctx)
    short_score = ENGINE._score_oi(make_signal(direction=Direction.SHORT), ctx)
    assert long_score == 10.0
    assert short_score == 0.0


def test_oi_short_buildup_backs_short_not_long() -> None:
    ctx = _oi_ctx(closes=(24010.0, 24005.0, 24000.0), oi_chg=1.0)
    long_score = ENGINE._score_oi(make_signal(direction=Direction.LONG), ctx)
    short_score = ENGINE._score_oi(make_signal(direction=Direction.SHORT), ctx)
    assert short_score == 10.0
    assert long_score == 0.0


def test_oi_short_covering_weakly_backs_long() -> None:
    ctx = _oi_ctx(closes=(23990.0, 23995.0, 24000.0), oi_chg=-1.0)
    assert ENGINE._score_oi(make_signal(direction=Direction.LONG), ctx) == 6.0
    assert ENGINE._score_oi(make_signal(direction=Direction.SHORT), ctx) == 3.0


def test_oi_flat_is_neutral() -> None:
    ctx = _oi_ctx(closes=(23990.0, 23995.0, 24000.0), oi_chg=0.1)
    assert ENGINE._score_oi(make_signal(direction=Direction.LONG), ctx) == 5.0


# ── structure component (BOS/CHoCH alignment) ────────────────────────


def _bos_up_15m() -> list:
    # Higher-high/higher-low swing sequence (fractal highs at 108/111, lows at
    # 101/104); the newest close breaks the latest swing high -> BOS_UP.
    closes = [100, 105, 103, 101, 103, 108, 106, 104, 106, 111, 109, 107, 112]
    return from_closes([float(p) for p in closes], half_range=0.4)


def test_structure_aligned_bos_beats_opposing() -> None:
    ctx = make_context(candles_15m=_bos_up_15m(), atr14_5m=10.0)
    long_score = ENGINE._score_structure(make_signal(direction=Direction.LONG), ctx)
    short_score = ENGINE._score_structure(make_signal(direction=Direction.SHORT), ctx)
    assert long_score - short_score == 7.0  # aligned BOS 7 vs opposing 0


def test_structure_neutral_without_15m_history() -> None:
    ctx = make_context(candles_15m=[], atr14_5m=10.0)
    score = ENGINE._score_structure(make_signal(direction=Direction.LONG), ctx)
    assert score >= 3.0  # neutral structure + ATR normality


# ── index alignment (dependency pairs) ───────────────────────────────


def test_index_alignment_rewards_confirming_proxy() -> None:
    signal = make_signal(direction=Direction.LONG)
    aligned_ctx = make_context()
    aligned_ctx.index_bias = Direction.LONG
    opposing_ctx = make_context()
    opposing_ctx.index_bias = Direction.SHORT
    neutral_ctx = make_context()
    assert ENGINE._score_index_alignment(signal, aligned_ctx) == 5.0
    assert ENGINE._score_index_alignment(signal, neutral_ctx) == 3.0
    assert ENGINE._score_index_alignment(signal, opposing_ctx) == 0.0


# ── level confluence with order-block / FVG zones ────────────────────


def test_unmitigated_bullish_zone_adds_confluence() -> None:
    # A down candle engulfed by a strong up close leaves a bullish order block
    # at 23995-24000; price then runs away without re-entering. A LONG entering
    # on the first tap back into the zone gets the extra confluence.
    zone_bars = [
        c(high=24005.0, low=24000.0, close=24001.0, open_=24004.0),   # filler
        c(high=24000.0, low=23995.0, close=23996.0, open_=23999.5),   # down bar (OB)
        c(high=24012.0, low=24004.0, close=24011.0, open_=24005.0),   # displacement
        c(high=24020.0, low=24013.0, close=24018.0, open_=24013.0),
        c(high=24025.0, low=24019.0, close=24024.0, open_=24019.0),
        c(high=24002.0, low=23996.0, close=23999.0, open_=24001.0),   # current: taps zone
    ]
    signal = make_signal(direction=Direction.LONG, entry=23998.0)
    with_zone = make_context(candles_15m=zone_bars, atr14_5m=4.0)
    without = make_context(candles_15m=[], atr14_5m=4.0)
    assert ENGINE._score_level_confluence(signal, with_zone) > ENGINE._score_level_confluence(
        signal, without
    )
