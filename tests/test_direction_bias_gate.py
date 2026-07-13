"""direction_bias_gate — suppress counter-trend signals in a decisive tape.

Mechanism validated on the 2026-07-13 window (cited in the commit): the tape
was LONG-biased all day and SHORT went 6/45 (13%, -5.6%) vs LONG 28/50 (56%,
+11.6%). These tests exercise the gate in isolation on the same mechanism.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.market_context import MarketDirection
from src.scanner import GateChain
from src.session.session_manager import SessionState
from src.signals.model import Direction, SetupClass
from tests.signal_factory import make_context, make_signal

IST = config.IST
_BASE_DT = IST.localize(datetime(2026, 7, 7, 0, 0, 0))


def _ist(h: int, m: int) -> datetime:
    return _BASE_DT + timedelta(hours=h, minutes=m)


def _ctx(direction_label: str):
    # NIFTY base so _index_conflict_gate is exempt and the market-direction
    # gate is the one under test; TRENDING_UP so the chop gate stays quiet.
    from src.regime import Regime

    ctx = make_context(base="NIFTY", regime_60m=Regime.TRENDING_UP)
    ctx.market_direction = direction_label
    return ctx


def test_suppresses_short_in_long_biased_tape() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.SHORT)
    result = chain.check(
        sig, _ctx(MarketDirection.LONG_BIASED), SessionState.OPEN, _ist(10, 0)
    )
    assert result == "direction_bias_gate"


def test_suppresses_long_in_short_biased_tape() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG)
    result = chain.check(
        sig, _ctx(MarketDirection.SHORT_BIASED), SessionState.OPEN, _ist(10, 0)
    )
    assert result == "direction_bias_gate"


def test_passes_with_trend_signal() -> None:
    chain = GateChain()
    sig = make_signal(direction=Direction.LONG)
    result = chain.check(
        sig, _ctx(MarketDirection.LONG_BIASED), SessionState.OPEN, _ist(10, 0)
    )
    assert result is None


def test_inert_on_neutral_direction() -> None:
    # A genuinely two-sided tape must not suppress either side.
    chain = GateChain()
    for d in (Direction.LONG, Direction.SHORT):
        result = chain.check(
            make_signal(direction=d),
            _ctx(MarketDirection.NEUTRAL),
            SessionState.OPEN,
            _ist(10, 0),
        )
        assert result is None


def test_exempt_setup_is_not_suppressed(monkeypatch) -> None:
    # Deliberately contrarian setups (e.g. PCR_EXTREME) can be exempted.
    import src.scanner as scanner

    monkeypatch.setattr(
        scanner,
        "_DIRECTION_GATE_EXEMPT_SETUPS",
        frozenset({SetupClass.PCR_EXTREME}),
    )
    chain = GateChain()
    sig = make_signal(
        direction=Direction.SHORT, setup_class=SetupClass.PCR_EXTREME
    )
    result = chain.check(
        sig, _ctx(MarketDirection.LONG_BIASED), SessionState.OPEN, _ist(10, 0)
    )
    assert result is None


def test_disabled_restores_current_behaviour(monkeypatch) -> None:
    import src.scanner as scanner

    monkeypatch.setattr(scanner, "_DIRECTION_BIAS_GATE_ENABLED", False)
    chain = GateChain()
    sig = make_signal(direction=Direction.SHORT)
    result = chain.check(
        sig, _ctx(MarketDirection.LONG_BIASED), SessionState.OPEN, _ist(10, 0)
    )
    assert result is None
