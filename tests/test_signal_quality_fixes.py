"""Session-15 signal-quality fixes, driven by live outcome data.

Covers: gate-chain rehydration after restart (the 40-signal day), the session
warm-up gate, the SL-noise gate, the dependency-pair index-conflict gate, the
per-setup flood cap, ORB/FAR opening-range lock, breakout chase guards, and
the tightened DIVERGENCE_CONTINUATION conditions.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import config
from src.channels.india_scalp import (
    DivergenceContinuation,
    FailedAuctionReclaim,
    OpeningRangeBreakout,
    VolumeSurgeBreakout,
)
from src.regime import Regime
from src.scanner import GateChain
from src.session.session_manager import SessionState
from src.signals.model import Direction, SetupClass
from tests.candle_factory import c, from_closes
from tests.signal_factory import make_context, make_signal

IST = config.IST
_NOW = IST.localize(datetime(2026, 7, 9, 11, 0, 0))


# ── Gate-chain rehydration (restart safety) ─────────────────────────


def _rows(n: int, base: str = "NIFTY", direction: str = "LONG", age: float = 3600.0):
    return [
        {
            "setup_class": SetupClass.TREND_PULLBACK_EMA,
            "base": base,
            "direction": direction,
            "age_sec": age,
        }
        for _ in range(n)
    ]


def test_rehydrate_restores_daily_cap() -> None:
    chain = GateChain()
    chain.rehydrate(_rows(10), _NOW)
    chain.begin_scan()
    sig = make_signal(base="RELIANCE")
    ctx = make_context(base="RELIANCE")
    gate = chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
    assert gate == "daily_cap_gate"


def test_rehydrate_restores_per_direction_cap() -> None:
    chain = GateChain()
    chain.rehydrate(_rows(2, base="NIFTY", direction="LONG"), _NOW)
    chain.begin_scan()
    sig = make_signal(direction=Direction.LONG)
    ctx = make_context(base="NIFTY")
    gate = chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
    assert gate == "duplicate_direction_gate"


def test_rehydrate_restores_cooldown() -> None:
    chain = GateChain()
    chain.rehydrate(_rows(1, age=60.0), _NOW)
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(base="NIFTY")
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate == "cooldown_gate"


def test_rehydrate_expired_cooldown_passes() -> None:
    chain = GateChain()
    chain.rehydrate(_rows(1, age=3600.0), _NOW)
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(base="NIFTY")
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate != "cooldown_gate"


def test_rehydrate_restores_direction_conflict_window() -> None:
    chain = GateChain()
    chain.rehydrate(_rows(1, direction="LONG", age=300.0), _NOW)
    chain.begin_scan()
    sig = make_signal(direction=Direction.SHORT)
    ctx = make_context(base="NIFTY")
    gate = chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
    assert gate == "direction_conflict_gate"


def test_rehydrate_empty_is_clean_slate() -> None:
    chain = GateChain()
    chain.rehydrate([], _NOW)
    chain.begin_scan()
    sig = make_signal()
    ctx = make_context()
    assert chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0) is None


# ── Warm-up gate ─────────────────────────────────────────────────────


def test_warmup_gate_suppresses_before_0930() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(scan_time_ist=time(9, 16))
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate == "warmup_gate"


def test_warmup_gate_passes_after_0930() -> None:
    chain = GateChain()
    sig = make_signal()
    ctx = make_context(scan_time_ist=time(9, 30))
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate != "warmup_gate"


# ── SL-noise gate ────────────────────────────────────────────────────


def test_sl_noise_gate_suppresses_sub_bar_stop() -> None:
    chain = GateChain()
    # ATR 10 -> floor 4.5 points; a 2-point stop is inside one bar's noise.
    sig = make_signal(entry=24000.0, sl=23998.0)
    ctx = make_context(atr14_5m=10.0)
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate == "sl_noise_gate"


def test_sl_noise_gate_passes_structural_stop() -> None:
    chain = GateChain()
    sig = make_signal(entry=24000.0, sl=23980.0)
    ctx = make_context(atr14_5m=10.0)
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate != "sl_noise_gate"


# ── Index-conflict gate (dependency pairs, enforced) ─────────────────


def test_index_conflict_gate_suppresses_stock_fighting_index() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG, base="RELIANCE")
    ctx = make_context(base="RELIANCE", atr14_5m=20.0)
    ctx.index_bias = Direction.SHORT
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate == "index_conflict_gate"


def test_index_conflict_gate_passes_aligned_stock() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG, base="RELIANCE")
    ctx = make_context(base="RELIANCE", atr14_5m=20.0)
    ctx.index_bias = Direction.LONG
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate is None


def test_index_conflict_gate_exempts_index_bases() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG, base="NIFTY")
    ctx = make_context(base="NIFTY")
    ctx.index_bias = Direction.SHORT
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate is None


def test_index_conflict_gate_neutral_bias_passes() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.SHORT, base="RELIANCE")
    # index_bias defaults NEUTRAL
    ctx = make_context(base="RELIANCE", atr14_5m=20.0)
    gate = chain.check(sig, ctx, SessionState.OPEN, _NOW)
    assert gate is None


# ── Setup flood cap ──────────────────────────────────────────────────


def test_setup_flood_gate_caps_same_setup_across_groups() -> None:
    chain = GateChain()
    chain.begin_scan()
    # First DIV short of the scan emits (INFY, IT group)...
    chain.record_emission(
        SetupClass.DIVERGENCE_CONTINUATION, "INFY", Direction.SHORT, _NOW
    )
    # ...a second DIV short in a *different* sector group is still capped.
    sig = make_signal(
        setup_class=SetupClass.DIVERGENCE_CONTINUATION,
        direction=Direction.SHORT,
        base="RELIANCE",
    )
    ctx = make_context(base="RELIANCE")
    gate = chain.check_emission(sig, ctx, _NOW, emitted_this_scan=1)
    assert gate == "setup_flood_gate"


def test_setup_flood_gate_resets_each_scan() -> None:
    chain = GateChain()
    chain.begin_scan()
    chain.record_emission(
        SetupClass.DIVERGENCE_CONTINUATION, "INFY", Direction.SHORT, _NOW
    )
    chain.begin_scan()
    sig = make_signal(
        setup_class=SetupClass.DIVERGENCE_CONTINUATION,
        direction=Direction.SHORT,
        base="RELIANCE",
    )
    ctx = make_context(base="RELIANCE")
    later = _NOW + timedelta(minutes=10)
    gate = chain.check_emission(sig, ctx, later, emitted_this_scan=0)
    assert gate != "setup_flood_gate"


def test_setup_flood_gate_allows_opposite_direction() -> None:
    chain = GateChain()
    chain.begin_scan()
    chain.record_emission(
        SetupClass.DIVERGENCE_CONTINUATION, "INFY", Direction.SHORT, _NOW
    )
    sig = make_signal(
        setup_class=SetupClass.DIVERGENCE_CONTINUATION,
        direction=Direction.LONG,
        base="RELIANCE",
    )
    ctx = make_context(base="RELIANCE")
    gate = chain.check_emission(sig, ctx, _NOW, emitted_this_scan=1)
    assert gate != "setup_flood_gate"


# ── ORB: opening-range lock + chase guard ────────────────────────────


def _orb_ctx(**overrides):
    current = c(high=24070.0, low=24050.0, close=24060.0, volume=2000.0)
    defaults = dict(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=[current],
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,
        opening_range_high=24050.0,
        opening_range_low=23950.0,
    )
    defaults.update(overrides)
    return make_context(**defaults)


def test_orb_requires_locked_opening_range() -> None:
    # Identical breakout: locked range fires, forming range must not — the
    # 2026-07-09 09:15 burst was six ORBs against ~30s of "range".
    assert OpeningRangeBreakout().evaluate(_orb_ctx()) is not None
    assert (
        OpeningRangeBreakout().evaluate(_orb_ctx(opening_range_locked=False))
        is None
    )


def test_orb_chase_guard_rejects_runaway_price() -> None:
    # Entry level 24052 (ORH + buffer); a close 30 points beyond it is more
    # than MAX_CHASE_ATR (0.5) x ATR 20 = 10 points -> unfillable, skip.
    runaway = c(high=24085.0, low=24050.0, close=24082.0, volume=2000.0)
    ctx = _orb_ctx(candles_5m=[runaway])
    assert OpeningRangeBreakout().evaluate(ctx) is None


# ── VSB chase guard ──────────────────────────────────────────────────


def test_vsb_chase_guard_rejects_runaway_price() -> None:
    # Swing high 24020.5 -> entry ~24021.5; close 24040 is ~18.5 points of
    # chase against a 10-point allowance.
    c15 = from_closes([24000.0, 23980.0, 24000.0, 24020.0, 24010.0])
    current = c(high=24045.0, low=23975.0, close=24040.0, volume=2500.0, open_=23980.0)
    ctx = make_context(
        regime_60m=Regime.TRENDING_UP,
        candles_5m=[c(high=24000.0, low=23995.0, close=23998.0, volume=1000.0), current],
        candles_15m=c15,
        atr14_5m=20.0,
        volume_avg_5m_20=1000.0,
        oi_change_15m_pct=1.0,
    )
    assert VolumeSurgeBreakout().evaluate(ctx) is None


# ── FAR: OR legs need the lock ───────────────────────────────────────


def test_far_or_levels_ignored_until_locked() -> None:
    or_high = 24050.0
    prev = c(high=24055.0, low=23990.0, close=24000.0, open_=24048.0)
    reclaim = c(high=24070.0, low=24040.0, close=24060.0, open_=24042.0, volume=2000.0)
    ctx = make_context(
        candles_5m=[
            c(high=24030.0, low=23980.0, close=24010.0),
            c(high=24040.0, low=23985.0, close=24020.0),
            c(high=24055.0, low=23990.0, close=24000.0, open_=24048.0),
            prev,
            reclaim,
        ],
        candles_15m=from_closes([24000.0 + i * 5 for i in range(10)]),
        opening_range_high=or_high,
        opening_range_low=23950.0,
        opening_range_locked=False,
        prev_day_high=24200.0,
        prev_day_low=23900.0,
        prev_day_close=24010.0,
        volume_avg_5m_20=1000.0,
        atr14_5m=100.0,
    )
    # Same fixture emits when the range is locked (test_far_long_emits);
    # with a forming range the OR leg must not produce a signal.
    assert FailedAuctionReclaim().evaluate(ctx) is None


# ── DIVERGENCE_CONTINUATION tightening ───────────────────────────────


def _div_candles():
    """Strong rally (stretched RSI at the prior peak), fade, weak new high,
    bearish engulfing rejection on the final bar."""
    closes = [23000.0 + i * 12 for i in range(26)]
    closes += [23300 - 4 * i for i in range(1, 6)]
    closes += [23280 - 2 * i for i in range(1, 5)]
    closes += [23272 + 9 * i for i in range(1, 6)]
    candles = from_closes(closes)
    candles[-2] = c(high=23317.0, low=23300.0, close=23316.0, open_=23305.0, volume=1200.0)
    candles[-1] = c(high=23320.0, low=23290.0, close=23295.0, open_=23317.0, volume=1500.0)
    return candles


def _div_ctx(candles):
    return make_context(
        regime_60m=Regime.RANGING,
        candles_5m=candles,
        candles_15m=from_closes([23000.0 + i * 10 for i in range(10)]),
        atr14_5m=30.0,
        prev_day_low=23050.0,
        prev_day_high=23400.0,
        prev_day_close=23100.0,
    )


def test_divergence_emits_at_genuine_exhaustion() -> None:
    sig = DivergenceContinuation().evaluate(_div_ctx(_div_candles()))
    assert sig is not None
    assert sig.direction == Direction.SHORT
    assert sig.setup_class == SetupClass.DIVERGENCE_CONTINUATION


def test_divergence_rejected_without_rejection_candle() -> None:
    candles = _div_candles()
    # Same geometry, but the final bar is a plain small red close (no pin /
    # engulfing) — the pre-Session-15 condition that mass-fired.
    candles[-1] = c(high=23320.0, low=23310.0, close=23314.0, open_=23318.0, volume=1500.0)
    assert DivergenceContinuation().evaluate(_div_ctx(candles)) is None


def test_divergence_rejected_when_prior_peak_not_stretched() -> None:
    # A drifting range: new high over prior high with weaker RSI, but the
    # prior peak never printed a stretched RSI — not exhaustion, just drift.
    closes = [23000.0 + (i % 4) * 3 for i in range(35)]
    closes += [23015.0, 23020.0, 23026.0, 23030.0, 23034.0]
    candles = from_closes(closes)
    candles[-1] = c(high=23036.0, low=23020.0, close=23022.0, open_=23034.0, volume=1500.0)
    assert DivergenceContinuation().evaluate(_div_ctx(candles)) is None
