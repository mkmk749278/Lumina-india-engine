"""LIQUIDITY_SWEEP_REVERSAL evaluator (spec §10.1)."""

from __future__ import annotations

from src.channels.india_scalp import LiquiditySweepReversal, _derive_tp2
from src.market.candle import Candle
from src.regime import Regime
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context

EVAL = LiquiditySweepReversal()

# 15m series with a swing low at 23979.5 (idx 1) and swing high at 24020.5 (idx 3).
C15 = from_closes([24000.0, 23980.0, 24000.0, 24020.0, 24010.0])


def _c5(current: Candle) -> list[Candle]:
    prior = c(high=23990.0, low=23980.0, close=23985.0, volume=1000.0)
    return [prior, current]


def test_long_sweep_reclaim_emits_signal() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        prev_day_low=23975.0,  # the swept swing sits on PDL (key-level rule)
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.setup_class == SetupClass.LIQUIDITY_SWEEP_REVERSAL
    assert sig.direction == Direction.LONG
    assert sig.sl < sig.entry < sig.tp1
    assert sig.rr_ratio >= 1.5
    assert sig.htf_trend_aligned is True  # RANGING is aligned for a reversal long
    assert sig.lot_size == 65  # NSE Jan-2026 rebaseline


def test_short_sweep_reclaim_emits_signal() -> None:
    sweep = c(high=24060.0, low=24010.0, close=24015.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        prev_day_high=24025.0,  # the swept swing sits on PDH (key-level rule)
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.SHORT
    assert sig.tp1 < sig.entry < sig.sl
    assert sig.rr_ratio >= 1.5


def test_low_volume_sweep_rejected() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=1000.0)  # == avg, not > 1.2x
    ctx = make_context(
        candles_5m=_c5(sweep), candles_15m=C15, atr14_5m=40.0, volume_avg_5m_20=1000.0
    )
    assert EVAL.evaluate(ctx) is None


def test_no_sweep_no_signal() -> None:
    # Trades inside the swing band: no level swept.
    inside = c(high=24010.0, low=23990.0, close=24000.0, volume=2000.0)
    ctx = make_context(
        candles_5m=_c5(inside), candles_15m=C15, atr14_5m=40.0, volume_avg_5m_20=1000.0
    )
    assert EVAL.evaluate(ctx) is None


def test_downtrend_regime_makes_long_unaligned() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.TRENDING_DOWN,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        prev_day_low=23975.0,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.direction == Direction.LONG
    assert sig.htf_trend_aligned is False  # long against a 60m downtrend


# ── Key-level requirement (Session 18: LSR went 0/6 sweeping nobody-swings) ──


def test_sweep_of_non_key_swing_rejected() -> None:
    # Same qualifying sweep as the emit test, but the swept swing (23979.5)
    # sits on no structural level (factory PDL 23900 / PDH 24100 / PDC 24010,
    # tolerance 0.25 x 40 ATR = 10 pts) — no resting-liquidity thesis.
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
    )
    assert EVAL.evaluate(ctx) is None


def test_key_level_requirement_kill_switch(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "LSR_REQUIRE_KEY_LEVEL", False)
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None  # requirement disabled — old behaviour


def test_vwap_extra_level_qualifies_sweep() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        key_levels_extra=[23979.0],  # session VWAP on the swept swing
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert "vwap" in sig.setup_reason


def test_unlocked_opening_range_does_not_qualify() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    kwargs = dict(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        opening_range_high=24040.0,
        opening_range_low=23979.0,  # on the swept swing
    )
    forming = make_context(**kwargs, opening_range_locked=False)
    assert EVAL.evaluate(forming) is None  # a forming range is not a level (IB17)
    locked = make_context(**kwargs)  # factory auto-locks a supplied range
    sig = EVAL.evaluate(locked)
    assert sig is not None
    assert "or_low" in sig.setup_reason


def test_key_level_tolerance_boundary() -> None:
    # Tolerance is 0.25 x ATR = 10 pts around the swept swing (23979.5).
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)

    def ctx_with_pdl(pdl: float):
        return make_context(
            regime_60m=Regime.RANGING,
            candles_5m=_c5(sweep),
            candles_15m=C15,
            atr14_5m=40.0,
            volume_avg_5m_20=1000.0,
            prev_day_low=pdl,
        )

    assert EVAL.evaluate(ctx_with_pdl(23979.5 - 10.0)) is not None  # at tolerance
    assert EVAL.evaluate(ctx_with_pdl(23979.5 - 10.6)) is None  # beyond it


def test_lsr_carries_pcr_at_entry() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        prev_day_low=23975.0,
        pcr=1.4,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.pcr_at_entry == 1.4


# ── TP2 derivation (owner-directed two-target plan, Session 18) ──


def test_tp2_r_multiple_fallback_when_no_level_in_band() -> None:
    # No structural level between 1.5x and 3x the TP1 distance -> 2x fallback.
    ctx = make_context()  # PDH 24100 / PDL 23900 / PDC 24010
    tp2 = _derive_tp2(ctx, entry=24000.0, tp1=24020.0, direction=Direction.LONG)
    assert tp2 == 24040.0  # entry + 2 x 20


def test_tp2_snaps_to_structural_level_in_band() -> None:
    # TP1 dist 40 -> band [60, 120] beyond entry; PDH at 24100 (dist 100) wins
    # over the 2x fallback (24080)... nearest in-band level is chosen.
    ctx = make_context(prev_day_high=24100.0)
    tp2 = _derive_tp2(ctx, entry=24000.0, tp1=24040.0, direction=Direction.LONG)
    assert tp2 == 24100.0


def test_tp2_short_direction_mirrored() -> None:
    ctx = make_context(prev_day_low=23900.0)
    # TP1 dist 40 -> band [60, 120] below entry; PDL 23900 (dist 100) in band.
    tp2 = _derive_tp2(ctx, entry=24000.0, tp1=23960.0, direction=Direction.SHORT)
    assert tp2 == 23900.0


def test_tp2_disabled_returns_zero(monkeypatch) -> None:
    import config

    monkeypatch.setattr(config, "TP2_ENABLED", False)
    ctx = make_context()
    assert _derive_tp2(ctx, 24000.0, 24040.0, Direction.LONG) == 0.0


def test_lsr_signal_carries_tp2_beyond_tp1() -> None:
    sweep = c(high=23990.0, low=23950.0, close=23985.0, volume=2000.0)
    ctx = make_context(
        regime_60m=Regime.RANGING,
        candles_5m=_c5(sweep),
        candles_15m=C15,
        atr14_5m=40.0,
        volume_avg_5m_20=1000.0,
        prev_day_low=23975.0,
    )
    sig = EVAL.evaluate(ctx)
    assert sig is not None
    assert sig.tp2 > sig.tp1 > sig.entry  # LONG: runner target beyond TP1
