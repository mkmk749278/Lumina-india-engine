"""Config invariants that encode non-negotiable business rules."""

from __future__ import annotations

import config


def test_auto_execution_hard_off_by_default() -> None:
    # OWNER_BRIEF IB2/IB10: never on until SEBI clearance + owner sign-off.
    assert config.AUTO_EXECUTION_ENABLED is False


def test_allowed_bases_index_only() -> None:
    # OWNER_BRIEF IB1: NIFTY + BANKNIFTY only at launch.
    assert config.ALLOWED_BASES == ("NIFTY", "BANKNIFTY")


def test_instruments_lot_sizes() -> None:
    # NSE Jan-2026 rebaseline (circular FAOP70616): NIFTY 75->65, BANKNIFTY 35->30.
    assert config.INSTRUMENTS["NIFTY"].lot_size == 65
    assert config.INSTRUMENTS["BANKNIFTY"].lot_size == 30


def test_instrument_round_steps() -> None:
    assert config.INSTRUMENTS["NIFTY"].round_step == 50.0
    assert config.INSTRUMENTS["BANKNIFTY"].round_step == 100.0


def test_session_clock_is_ordered() -> None:
    assert (
        config.PREOPEN_START
        < config.MARKET_OPEN
        < config.LAST_SIGNAL_TIME
        < config.FORCE_CLOSE_TIME
        < config.MARKET_CLOSE
    )


def test_safe_bool_parses_truthy_and_falsy(monkeypatch) -> None:
    monkeypatch.setenv("X_FLAG", "yes")
    assert config._safe_bool("X_FLAG", False) is True
    monkeypatch.setenv("X_FLAG", "off")
    assert config._safe_bool("X_FLAG", True) is False
    monkeypatch.delenv("X_FLAG", raising=False)
    assert config._safe_bool("X_FLAG", True) is True


def test_safe_time_parses_hh_mm(monkeypatch) -> None:
    from datetime import time

    monkeypatch.setenv("X_TIME", "09:45")
    assert config._safe_time("X_TIME", time(0, 0)) == time(9, 45)
    monkeypatch.setenv("X_TIME", "garbage")
    assert config._safe_time("X_TIME", time(1, 2)) == time(1, 2)
