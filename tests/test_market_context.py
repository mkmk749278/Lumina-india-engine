"""MarketContext — the per-scan market-wide regime vector (Phase 1)."""

from __future__ import annotations

from datetime import datetime, time

import config
from src.market_context import (
    MarketContext,
    MarketDirection,
    SessionPhase,
    VixRegime,
    classify_market_direction,
    classify_session_phase,
    classify_vix_regime,
)
from src.regime import Regime
from tests.candle_factory import from_closes
from tests.signal_factory import make_context


def _index(base: str, closes: list[float], day_open: float, **kw):
    """An index context with enough 5m bars for market_bias to speak. Neutralise
    the opening-gap vote by default (prev_close == open) so direction tests
    isolate the vote under test; gap/FII tests set these explicitly."""
    kw.setdefault("prev_day_close", day_open)
    return make_context(
        base=base,
        symbol=f"NSE:{base}26JULFUT",
        candles_5m=from_closes(closes),
        day_open=day_open,
        **kw,
    )


def test_fii_dii_vote_supplies_a_direction_vote():
    from src.market_context import _fii_dii_vote

    assert _fii_dii_vote(1200.0) == "LONG"   # strong net buying
    assert _fii_dii_vote(-1200.0) == "SHORT"  # strong net selling
    assert _fii_dii_vote(0.0) == "NEUTRAL"    # unavailable → never fabricated
    assert _fii_dii_vote(100.0) == "NEUTRAL"  # below the MIN_CR threshold


def test_opening_gap_vote():
    from src.market_context import _open_gap_vote

    assert _open_gap_vote(0.6) == "LONG"   # gap up
    assert _open_gap_vote(-0.6) == "SHORT"  # gap down
    assert _open_gap_vote(0.1) == "NEUTRAL"


def test_fii_dii_can_complete_a_long_bias():
    # NIFTY intraday LONG + a big FII/DII buy = two aligned votes, zero opposing.
    ctxs = {
        "NIFTY": _index(
            "NIFTY", [23900 + i * 10 for i in range(25)], 23900,
            fii_dii_net_cr=2000.0,
        ),
    }
    assert classify_market_direction(ctxs) == MarketDirection.LONG_BIASED


# --- session phase --------------------------------------------------------

def test_session_phase_boundaries():
    assert classify_session_phase(time(9, 0)) == SessionPhase.PREOPEN
    assert classify_session_phase(time(9, 20)) == SessionPhase.POWER_HOUR
    assert classify_session_phase(time(10, 29)) == SessionPhase.POWER_HOUR
    # 10:30 default boundary → midday chop (the 2026-07-13 dead zone)
    assert classify_session_phase(time(11, 30)) == SessionPhase.MIDDAY_CHOP
    assert classify_session_phase(time(13, 29)) == SessionPhase.MIDDAY_CHOP
    assert classify_session_phase(time(14, 0)) == SessionPhase.CLOSING
    assert classify_session_phase(time(15, 30)) == SessionPhase.CLOSING
    assert classify_session_phase(time(15, 31)) == SessionPhase.CLOSED
    assert classify_session_phase(None) == SessionPhase.CLOSED


# --- vix regime -----------------------------------------------------------

def test_vix_regime_bands():
    assert classify_vix_regime(0.0) == VixRegime.UNKNOWN  # stale/unavailable
    assert classify_vix_regime(13.3) == VixRegime.LOW  # the 2026-07-13 tape
    assert classify_vix_regime(16.0) == VixRegime.NORMAL
    assert classify_vix_regime(22.0) == VixRegime.ELEVATED
    assert classify_vix_regime(27.0) == VixRegime.EXTREME


# --- market direction -----------------------------------------------------

def test_direction_long_biased_needs_two_aligned_votes():
    ctxs = {
        "NIFTY": _index("NIFTY", [23900 + i * 10 for i in range(25)], 23900),
        "BANKNIFTY": _index(
            "BANKNIFTY", [58000 + i * 20 for i in range(25)], 58000
        ),
    }
    assert classify_market_direction(ctxs) == MarketDirection.LONG_BIASED


def test_direction_short_biased():
    ctxs = {
        "NIFTY": _index("NIFTY", [24100 - i * 10 for i in range(25)], 24100),
        "BANKNIFTY": _index(
            "BANKNIFTY", [58500 - i * 20 for i in range(25)], 58500
        ),
    }
    assert classify_market_direction(ctxs) == MarketDirection.SHORT_BIASED


def test_direction_neutral_when_indices_conflict():
    # NIFTY up, BANKNIFTY down → one long, one short, no daily trend → NEUTRAL.
    ctxs = {
        "NIFTY": _index("NIFTY", [23900 + i * 10 for i in range(25)], 23900),
        "BANKNIFTY": _index(
            "BANKNIFTY", [58500 - i * 20 for i in range(25)], 58500
        ),
    }
    assert classify_market_direction(ctxs) == MarketDirection.NEUTRAL


def test_direction_daily_regime_supplies_the_second_vote():
    # NIFTY intraday LONG + NIFTY daily TRENDING_UP = two long votes even with
    # BANKNIFTY absent.
    ctxs = {
        "NIFTY": _index(
            "NIFTY",
            [23900 + i * 10 for i in range(25)],
            23900,
            regime_daily=Regime.TRENDING_UP,
        ),
    }
    assert classify_market_direction(ctxs) == MarketDirection.LONG_BIASED


def test_direction_empty_is_neutral():
    assert classify_market_direction({}) == MarketDirection.NEUTRAL


# --- MarketContext.build integration --------------------------------------

def test_build_folds_indices_into_the_vector():
    now = config.IST.localize(datetime(2026, 7, 13, 11, 40))  # midday chop
    ctxs = {
        "NIFTY": _index(
            "NIFTY",
            [23900 + i * 10 for i in range(25)],
            23900,
            india_vix=13.3,
            pcr=0.82,
        ),
        "BANKNIFTY": _index(
            "BANKNIFTY", [58000 + i * 20 for i in range(25)], 58000
        ),
    }
    mc = MarketContext.build(ctxs, now)
    assert mc.session_phase == SessionPhase.MIDDAY_CHOP
    assert mc.vix_regime == VixRegime.LOW
    assert mc.market_direction == MarketDirection.LONG_BIASED
    assert mc.pcr == 0.82
    assert mc.vix == 13.3
    assert mc.leader in ("NIFTY", "BANKNIFTY")


def test_build_handles_no_indices():
    now = config.IST.localize(datetime(2026, 7, 13, 11, 40))
    mc = MarketContext.build({}, now)
    assert mc.market_direction == MarketDirection.NEUTRAL
    assert mc.vix_regime == VixRegime.UNKNOWN
    assert mc.session_phase == SessionPhase.MIDDAY_CHOP
