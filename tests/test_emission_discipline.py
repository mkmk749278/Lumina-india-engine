"""Emission-discipline gates (Session 21) — all default OFF/inert.

phase_affinity_gate, duplicate_entry_gate, allocator_suppress_gate.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import config
import src.scanner as scanner_mod
from src.regime import Regime
from src.scanner import GateChain
from src.session.session_manager import SessionState
from src.signals.model import IndiaContext, IndiaSignal


def _now() -> datetime:
    return config.IST.localize(datetime(2026, 7, 6, 10, 0))


_TEN_AM = time(10, 0)


def _ctx(scan_time: time = _TEN_AM) -> IndiaContext:
    return IndiaContext(
        base="NIFTY",
        regime_60m=Regime.TRENDING_UP,
        regime_daily=Regime.TRENDING_UP,
        candles_5m=[],
        volume_avg_5m_20=1000.0,
        atr14_5m=15.0,
        prev_day_high=0.0,
        prev_day_low=0.0,
        prev_day_close=0.0,
        oi_change_15m_pct=0.0,
        india_vix=13.0,
        scan_time_ist=scan_time,
    )


def _sig(setup: str = "TREND_PULLBACK_EMA", entry: float = 24500.0) -> IndiaSignal:
    return IndiaSignal(
        signal_id=f"e-{setup}-{entry}",
        symbol="NSE:NIFTY26JULFUT",
        base="NIFTY",
        direction="LONG",
        setup_class=setup,
        entry=entry,
        sl=entry - 30,
        tp1=entry + 60,
        sl_pct=0.12,
        tp1_pct=0.24,
        rr_ratio=2.0,
        lot_size=65,
    )


# ── phase-affinity gate ──────────────────────────────────────────────


def test_phase_gate_off_by_default() -> None:
    gates = GateChain()
    assert (
        gates._phase_affinity_gate(_sig(), _ctx(), SessionState.OPEN, _now())
        is None
    )


def test_phase_gate_blocks_listed_pair_only(monkeypatch) -> None:
    monkeypatch.setattr(config, "PHASE_GATE_ENABLED", True)
    monkeypatch.setattr(
        scanner_mod,
        "_PHASE_BLOCKLIST",
        frozenset({("TREND", "POWER_HOUR")}),
    )
    gates = GateChain()
    # TREND family at 10:00 (POWER_HOUR) → blocked.
    reason = gates._phase_affinity_gate(
        _sig("TREND_PULLBACK_EMA"), _ctx(time(10, 0)), SessionState.OPEN, _now()
    )
    assert reason is not None and "POWER_HOUR" in reason
    # Same family at midday → passes.
    assert (
        gates._phase_affinity_gate(
            _sig("TREND_PULLBACK_EMA"), _ctx(time(12, 0)),
            SessionState.OPEN, _now(),
        )
        is None
    )
    # Non-listed family in the same phase → passes.
    assert (
        gates._phase_affinity_gate(
            _sig("LIQUIDITY_SWEEP_REVERSAL"), _ctx(time(10, 0)),
            SessionState.OPEN, _now(),
        )
        is None
    )


def test_phase_blocklist_parser_fails_open() -> None:
    parsed = scanner_mod._parse_phase_blocklist("TREND:POWER_HOUR, junk,, :X")
    assert parsed == frozenset({("TREND", "POWER_HOUR")})


# ── duplicate entry-move gate ────────────────────────────────────────


def test_dup_entry_gate_inert_at_default_zero() -> None:
    gates = GateChain()
    gates.record_emission("TPE", "NIFTY", "LONG", _now(), entry=24500.0)
    gates.begin_scan()  # a re-fire is always a later scan
    assert (
        gates.check_emission(_sig(entry=24501.0), _ctx(), _now(), 0) is None
    )


def test_dup_entry_gate_blocks_echo_and_allows_new_structure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "DUP_MIN_ENTRY_MOVE_ATR", 1.0)  # 15 pts
    gates = GateChain()
    gates.record_emission("TPE", "NIFTY", "LONG", _now(), entry=24500.0)
    gates.begin_scan()  # a re-fire is always a later scan
    echo = gates.check_emission(_sig(entry=24505.0), _ctx(), _now(), 0)
    assert echo == "duplicate_entry_gate"
    fresh = gates.check_emission(
        _sig(entry=24530.0), _ctx(), _now() + timedelta(minutes=20), 0
    )
    assert fresh is None


# ── allocator suppress ───────────────────────────────────────────────


def test_allocator_dark_mode_never_suppresses() -> None:
    gates = GateChain()
    gates.set_allocator_suppress(frozenset({"TREND_PULLBACK_EMA/LONG"}))
    assert gates.check_emission(_sig(), _ctx(), _now(), 0) is None


def test_allocator_armed_suppresses_listed_cohort(monkeypatch) -> None:
    monkeypatch.setattr(config, "ALLOCATOR_ARMED", True)
    gates = GateChain()
    gates.set_allocator_suppress(frozenset({"TREND_PULLBACK_EMA/LONG"}))
    assert (
        gates.check_emission(_sig(), _ctx(), _now(), 0)
        == "allocator_suppress_gate"
    )
    # Non-listed cohort untouched.
    assert (
        gates.check_emission(
            _sig("VOLUME_SURGE_BREAKOUT"), _ctx(), _now(), 0
        )
        is None
    )
