"""Scoring v2 (Session 21, shadow) — the de-correlated model.

Pins the properties the v1 inversion violated: the four trend restatements
can no longer stack, extension is penalised, phase affinity and freshness
are priced, and the budget stays in [0, 100].
"""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.market.candle import Candle
from src.regime import Regime
from src.signal_quality import IndiaSignalScoringEngine
from src.signals.model import Direction, IndiaContext, IndiaSignal


def _candles(closes: list[float], tf_min: int = 5) -> list[Candle]:
    base = config.IST.localize(datetime(2026, 7, 6, 9, 15))
    return [
        Candle(
            ts=base + timedelta(minutes=tf_min * i),
            open=c,
            high=c + 3,
            low=c - 3,
            close=c,
            volume=1000,
        )
        for i, c in enumerate(closes)
    ]


def _ctx(**over) -> IndiaContext:
    closes = [24400.0 + i * 8 for i in range(30)]
    defaults = dict(
        base="NIFTY",
        regime_60m=Regime.TRENDING_UP,
        regime_daily=Regime.TRENDING_UP,
        candles_5m=_candles(closes),
        volume_avg_5m_20=1000.0,
        atr14_5m=15.0,
        prev_day_high=24700.0,
        prev_day_low=24300.0,
        prev_day_close=24500.0,
        oi_change_15m_pct=1.0,
        india_vix=13.0,
        candles_15m=_candles(closes, 15),
        day_open=24400.0,
        session_vwap=24500.0,
        ema21_5m=24550.0,
        index_bias=Direction.LONG,
        market_direction="LONG_BIASED",
    )
    defaults.update(over)
    return IndiaContext(**defaults)


def _signal(entry: float = 24630.0) -> IndiaSignal:
    return IndiaSignal(
        signal_id="s2",
        symbol="NSE:NIFTY26JULFUT",
        base="NIFTY",
        direction="LONG",
        setup_class="TREND_PULLBACK_EMA",
        entry=entry,
        sl=entry - 30.0,
        tp1=entry + 60.0,
        sl_pct=0.12,
        tp1_pct=0.24,
        rr_ratio=2.0,
        lot_size=65,
        htf_trend_aligned=True,
        tp2=entry + 120.0,
    )


def test_trend_restatements_no_longer_stack() -> None:
    """Fully trend-aligned TPE long: v1 banks regime+HTF+BOS+index ≈ up to
    39; v2's trend_establishment is capped at 15."""
    eng = IndiaSignalScoringEngine()
    _, comps = eng.score_v2(_signal(), _ctx(), session_phase="CLOSING")
    assert comps["trend_establishment"] <= 15.0


def test_extension_penalty_kicks_in_beyond_half_atr() -> None:
    eng = IndiaSignalScoringEngine()
    near = _signal(entry=24505.0)  # ~0.3 ATR above VWAP (15 ATR)
    far = _signal(entry=24560.0)  # 4 ATR above VWAP
    _, comps_near = eng.score_v2(near, _ctx(), session_phase="CLOSING")
    _, comps_far = eng.score_v2(far, _ctx(), session_phase="CLOSING")
    assert comps_near["extension_penalty"] == 0.0
    assert comps_far["extension_penalty"] <= -10.0 + 1e-9


def test_extended_scores_below_fresh_pullback() -> None:
    """The v1 inversion in one assertion: same setup, the extended entry must
    score lower than the near-VWAP entry."""
    eng = IndiaSignalScoringEngine()
    near, _ = eng.score_v2(_signal(entry=24505.0), _ctx(), session_phase="CLOSING")
    far, _ = eng.score_v2(_signal(entry=24560.0), _ctx(), session_phase="CLOSING")
    assert near > far


def test_phase_affinity_follows_doctrine() -> None:
    eng = IndiaSignalScoringEngine()
    sig = _signal()
    # TREND family: closing 8, midday 2.
    _, closing = eng.score_v2(sig, _ctx(), session_phase="CLOSING")
    _, midday = eng.score_v2(sig, _ctx(), session_phase="MIDDAY_CHOP")
    assert closing["phase_affinity"] == 8.0
    assert midday["phase_affinity"] == 2.0
    # Unknown phase → neutral midpoint, never fabricated.
    _, unknown = eng.score_v2(sig, _ctx(), session_phase="")
    assert unknown["phase_affinity"] == 4.0


def test_freshness_prefers_young_aligned_bias() -> None:
    eng = IndiaSignalScoringEngine()
    sig = _signal()
    _, young = eng.score_v2(
        sig, _ctx(), session_phase="CLOSING", bias_age_min=10.0
    )
    _, stale = eng.score_v2(
        sig, _ctx(), session_phase="CLOSING", bias_age_min=180.0
    )
    _, unknown = eng.score_v2(
        sig, _ctx(), session_phase="CLOSING", bias_age_min=-1.0
    )
    assert young["freshness"] == 7.0
    assert stale["freshness"] == 2.0
    assert unknown["freshness"] == 3.5


def test_duplicate_penalty() -> None:
    eng = IndiaSignalScoringEngine()
    sig = _signal()
    first, c1 = eng.score_v2(sig, _ctx(), session_phase="CLOSING", dup_index=1)
    second, c2 = eng.score_v2(sig, _ctx(), session_phase="CLOSING", dup_index=2)
    assert c1["dup_penalty"] == 0.0
    assert c2["dup_penalty"] == -3.0
    assert second < first


def test_score_bounded_and_components_returned() -> None:
    eng = IndiaSignalScoringEngine()
    total, comps = eng.score_v2(
        _signal(), _ctx(), session_phase="CLOSING", bias_age_min=10.0
    )
    assert 0.0 <= total <= 100.0
    assert abs(sum(comps.values()) - total) < 0.5 or total in (0.0, 100.0)
