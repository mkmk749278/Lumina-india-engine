"""Regime/setup-compatibility gate + per-setup-per-day diversity cap.

Both land in the signal-quality tuning after the 2026-07-14 review: trend-
continuation setups in a non-trending daily regime were the day's loss cohort
(3/23, -3.76%), and one setup (TREND_PULLBACK_EMA) was 41% of the day's volume.
"""
from __future__ import annotations

from datetime import datetime

import config
from src.regime import Regime
from src.scanner import GateChain
from src.session.session_manager import SessionState
from src.signals.model import Direction, SetupClass, is_trend_family
from tests.signal_factory import make_context, make_signal

IST = config.IST
_NOW = IST.localize(datetime(2026, 7, 14, 11, 0, 0))


# ── taxonomy ─────────────────────────────────────────────────────────


def test_setup_family_covers_all_setups() -> None:
    from src.signals.model import SETUP_FAMILY

    setups = {
        v for k, v in vars(SetupClass).items() if not k.startswith("_")
    }
    assert setups == set(SETUP_FAMILY), "every SetupClass needs a family"


def test_trend_family_membership() -> None:
    assert is_trend_family(SetupClass.TREND_PULLBACK_EMA)
    assert is_trend_family(SetupClass.BREAKDOWN_SHORT)
    assert not is_trend_family(SetupClass.SR_FLIP_RETEST)
    assert not is_trend_family(SetupClass.OPENING_RANGE_BREAKOUT)
    assert not is_trend_family(SetupClass.PCR_EXTREME)


# ── regime/setup gate ────────────────────────────────────────────────


def _check(sig, ctx) -> str | None:
    return GateChain().check(sig, ctx, SessionState.OPEN, _NOW)


def test_trend_setup_suppressed_in_ranging_daily() -> None:
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(regime_60m=Regime.TRENDING_UP, regime_daily=Regime.RANGING)
    assert _check(sig, ctx) == "regime_setup_gate"


def test_trend_setup_suppressed_in_quiet_daily() -> None:
    sig = make_signal(setup_class=SetupClass.BREAKDOWN_SHORT, direction=Direction.SHORT)
    ctx = make_context(regime_60m=Regime.TRENDING_DOWN, regime_daily=Regime.QUIET)
    assert _check(sig, ctx) == "regime_setup_gate"


def test_trend_setup_passes_in_trending_daily() -> None:
    # A trend-pullback in a trending daily with a consolidating 60m is a
    # healthy pullback entry — must NOT be gated.
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(regime_60m=Regime.RANGING, regime_daily=Regime.TRENDING_UP)
    assert _check(sig, ctx) is None


def test_reversion_setup_passes_in_ranging_daily() -> None:
    sig = make_signal(setup_class=SetupClass.SR_FLIP_RETEST)
    ctx = make_context(regime_60m=Regime.TRENDING_UP, regime_daily=Regime.RANGING)
    assert _check(sig, ctx) is None


def test_breakout_setup_passes_in_ranging_daily() -> None:
    sig = make_signal(setup_class=SetupClass.OPENING_RANGE_BREAKOUT)
    ctx = make_context(regime_60m=Regime.TRENDING_UP, regime_daily=Regime.RANGING)
    assert _check(sig, ctx) is None


def test_regime_setup_gate_kill_switch(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_REGIME_SETUP_GATE_ENABLED", False)
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(regime_60m=Regime.TRENDING_UP, regime_daily=Regime.RANGING)
    assert GateChain().check(sig, ctx, SessionState.OPEN, _NOW) != "regime_setup_gate"


def test_regime_setup_gate_exempt_setup(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(
        scanner_mod,
        "_REGIME_SETUP_EXEMPT_SETUPS",
        frozenset({SetupClass.TREND_PULLBACK_EMA}),
    )
    sig = make_signal(setup_class=SetupClass.TREND_PULLBACK_EMA)
    ctx = make_context(regime_60m=Regime.TRENDING_UP, regime_daily=Regime.RANGING)
    assert GateChain().check(sig, ctx, SessionState.OPEN, _NOW) != "regime_setup_gate"


# ── per-setup-per-day diversity cap ──────────────────────────────────


def test_setup_diversity_cap_suppresses_over_limit(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_MAX_PER_SETUP_PER_DAY", 3)
    chain = GateChain()
    setup = SetupClass.SR_FLIP_RETEST
    # Emit the same setup on 3 distinct bases across 3 scans — all allowed
    # (distinct bases + fresh scan sidestep the per-base and per-scan caps, so
    # only the per-day setup cap is under test).
    for i in range(3):
        chain.begin_scan()
        base = f"STOCK{i}"
        sig = make_signal(setup_class=setup, base=base)
        ctx = make_context(base=base)
        assert chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0) is None
        chain.record_emission(setup, base, Direction.LONG, _NOW)
    # 4th distinct base, fresh scan — only the per-day setup cap should bite.
    chain.begin_scan()
    sig = make_signal(setup_class=setup, base="STOCK9")
    ctx = make_context(base="STOCK9")
    assert (
        chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
        == "setup_diversity_gate"
    )


def test_setup_diversity_cap_is_per_setup(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_MAX_PER_SETUP_PER_DAY", 2)
    chain = GateChain()
    for i in range(2):
        chain.record_emission(SetupClass.SR_FLIP_RETEST, f"B{i}", Direction.LONG, _NOW)
    # A different setup is unaffected by the first setup's count.
    other = make_signal(setup_class=SetupClass.VOLUME_SURGE_BREAKOUT, base="TCS")
    ctx = make_context(base="TCS", regime_daily=Regime.TRENDING_UP)
    assert chain.check_emission(other, ctx, _NOW, emitted_this_scan=0) is None


def test_setup_diversity_cap_resets_next_day(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_MAX_PER_SETUP_PER_DAY", 1)
    chain = GateChain()
    chain.begin_scan()
    chain.record_emission(SetupClass.SR_FLIP_RETEST, "STOCK0", Direction.LONG, _NOW)
    # Fresh scan + a distinct base so only the per-day setup cap is in play.
    chain.begin_scan()
    sig = make_signal(setup_class=SetupClass.SR_FLIP_RETEST, base="STOCK1")
    ctx = make_context(base="STOCK1")
    assert (
        chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
        == "setup_diversity_gate"
    )
    chain.reset_day()
    chain.begin_scan()
    assert chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0) is None


def test_setup_diversity_cap_off_when_zero(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_MAX_PER_SETUP_PER_DAY", 0)
    chain = GateChain()
    for _ in range(50):
        chain.record_emission(SetupClass.SR_FLIP_RETEST, "NIFTY", Direction.LONG, _NOW)
    sig = make_signal(setup_class=SetupClass.SR_FLIP_RETEST)
    ctx = make_context()
    assert (
        chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
        != "setup_diversity_gate"
    )


def test_setup_diversity_cap_rehydrates(monkeypatch) -> None:
    import src.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod, "_MAX_PER_SETUP_PER_DAY", 2)
    chain = GateChain()
    rows = [
        {
            "setup_class": SetupClass.SR_FLIP_RETEST,
            "base": f"B{i}",
            "direction": Direction.LONG,
            "age_sec": 3600.0,
        }
        for i in range(2)
    ]
    chain.rehydrate(rows, _NOW)
    chain.begin_scan()
    sig = make_signal(setup_class=SetupClass.SR_FLIP_RETEST)
    ctx = make_context()
    assert (
        chain.check_emission(sig, ctx, _NOW, emitted_this_scan=0)
        == "setup_diversity_gate"
    )
