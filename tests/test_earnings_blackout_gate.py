"""earnings_blackout_gate (G8) — suppress single-stock signals in their own
results window; index bases exempt; inert when the calendar is unpopulated."""

from __future__ import annotations

import json
from datetime import datetime

import config
from src.scanner import GateChain
from src.session.earnings_calendar import EarningsCalendar
from src.session.session_manager import SessionState
from src.signals.model import Direction
from tests.signal_factory import make_context, make_signal

IST = config.IST


def _cal(tmp_path, mapping: dict, *, verified: bool = True) -> EarningsCalendar:
    p = tmp_path / "earnings.json"
    p.write_text(json.dumps({"verified": verified, "earnings": mapping}))
    return EarningsCalendar(str(p))


def _chain(cal: EarningsCalendar) -> GateChain:
    return GateChain(earnings=cal)


def _at(y: int, mo: int, d: int, h: int = 10, mi: int = 0) -> datetime:
    return IST.localize(datetime(y, mo, d, h, mi))


def test_suppresses_stock_on_results_day(tmp_path) -> None:
    chain = _chain(_cal(tmp_path, {"INFY": ["2026-07-16"]}))
    sig = make_signal(base="INFY", direction=Direction.LONG)
    res = chain.check(
        sig, make_context(base="INFY"), SessionState.OPEN, _at(2026, 7, 16)
    )
    assert res == "earnings_blackout_gate"


def test_blackout_window_covers_day_before_and_after(tmp_path) -> None:
    chain = _chain(_cal(tmp_path, {"INFY": ["2026-07-16"]}))
    for day in (15, 17):  # DAYS_BEFORE=1, DAYS_AFTER=1
        res = chain.check(
            make_signal(base="INFY"),
            make_context(base="INFY"),
            SessionState.OPEN,
            _at(2026, 7, day),
        )
        assert res == "earnings_blackout_gate", day


def test_passes_outside_window(tmp_path) -> None:
    chain = _chain(_cal(tmp_path, {"INFY": ["2026-07-16"]}))
    res = chain.check(
        make_signal(base="INFY"),
        make_context(base="INFY"),
        SessionState.OPEN,
        _at(2026, 7, 20),
    )
    assert res != "earnings_blackout_gate"


def test_index_base_is_exempt(tmp_path) -> None:
    # An index can't have a single-stock earnings date even if one is listed.
    chain = _chain(_cal(tmp_path, {"NIFTY": ["2026-07-16"]}))
    res = chain.check(
        make_signal(base="NIFTY"),
        make_context(base="NIFTY"),
        SessionState.OPEN,
        _at(2026, 7, 16),
    )
    assert res != "earnings_blackout_gate"


def test_other_stock_not_blacked_out(tmp_path) -> None:
    chain = _chain(_cal(tmp_path, {"INFY": ["2026-07-16"]}))
    res = chain.check(
        make_signal(base="TCS"),
        make_context(base="TCS"),
        SessionState.OPEN,
        _at(2026, 7, 16),
    )
    assert res != "earnings_blackout_gate"


def test_disabled_flag_restores_behaviour(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "EARNINGS_BLACKOUT_ENABLED", False)
    chain = _chain(_cal(tmp_path, {"INFY": ["2026-07-16"]}))
    res = chain.check(
        make_signal(base="INFY"),
        make_context(base="INFY"),
        SessionState.OPEN,
        _at(2026, 7, 16),
    )
    assert res != "earnings_blackout_gate"


def test_empty_calendar_is_inert(tmp_path) -> None:
    chain = _chain(_cal(tmp_path, {}, verified=False))
    res = chain.check(
        make_signal(base="INFY"),
        make_context(base="INFY"),
        SessionState.OPEN,
        _at(2026, 7, 16),
    )
    assert res != "earnings_blackout_gate"


def test_custom_window_widths(tmp_path) -> None:
    cal = EarningsCalendar(
        str(_write(tmp_path, {"INFY": ["2026-07-16"]})),
        days_before=3,
        days_after=0,
    )
    chain = _chain(cal)
    # 3 days before is inside the window...
    assert (
        chain.check(
            make_signal(base="INFY"),
            make_context(base="INFY"),
            SessionState.OPEN,
            _at(2026, 7, 13),
        )
        == "earnings_blackout_gate"
    )
    # ...one day after is not (days_after=0).
    assert (
        chain.check(
            make_signal(base="INFY"),
            make_context(base="INFY"),
            SessionState.OPEN,
            _at(2026, 7, 17),
        )
        != "earnings_blackout_gate"
    )


def _write(tmp_path, mapping: dict):
    p = tmp_path / "earnings2.json"
    p.write_text(json.dumps({"verified": True, "earnings": mapping}))
    return p
