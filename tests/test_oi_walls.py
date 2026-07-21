"""Option-chain OI walls as S/R (G5): compute the call/put walls, expose them
on context, and consume them in level confluence + structural targets."""

from __future__ import annotations

from src.channels.india_scalp import _oi_levels, _structural_tp1
from src.data.india_market_data import IndiaMarketData
from src.signal_quality import IndiaSignalScoringEngine
from src.signals.model import Direction
from tests.signal_factory import make_context


def test_computes_walls_from_chain() -> None:
    mkt = IndiaMarketData()
    strikes = [24000.0, 24100.0, 24200.0, 24300.0]
    call_oi = [10.0, 20.0, 90.0, 15.0]  # heaviest call OI at 24200 -> resistance
    put_oi = [80.0, 30.0, 10.0, 5.0]    # heaviest put OI at 24000 -> support
    call_wall, put_wall = mkt.compute_and_set_oi_walls(
        "NIFTY", strikes, call_oi, put_oi
    )
    assert call_wall == 24200.0
    assert put_wall == 24000.0
    assert mkt.get_oi_walls("NIFTY") == (24200.0, 24000.0)


def test_empty_chain_returns_no_walls() -> None:
    mkt = IndiaMarketData()
    assert mkt.compute_and_set_oi_walls("NIFTY", [], [], []) == (0.0, 0.0)
    assert mkt.get_oi_walls("NIFTY") == (None, None)


def test_zero_oi_is_not_a_wall() -> None:
    mkt = IndiaMarketData()
    call_wall, put_wall = mkt.compute_and_set_oi_walls(
        "NIFTY", [24000.0, 24100.0], [0.0, 0.0], [0.0, 0.0]
    )
    assert (call_wall, put_wall) == (0.0, 0.0)


def test_oi_levels_helper_filters_none() -> None:
    ctx = make_context(base="NIFTY")
    assert _oi_levels(ctx) == []  # defaults are None
    ctx.call_oi_wall = 24200.0
    ctx.put_oi_wall = 24000.0
    ctx.max_pain_strike = 24100.0
    assert set(_oi_levels(ctx)) == {24200.0, 24000.0, 24100.0}


def test_wall_used_as_structural_tp1_anchor() -> None:
    # entry 24000, sl_dist 20, 2R fallback = 24040. A call wall at 24030 (1.5R)
    # is a real resistance target inside the band -> TP1 snaps to it.
    ctx = make_context(
        base="NIFTY", prev_day_high=0.0, prev_day_low=0.0,
        prev_day_close=0.0, key_levels_extra=[],
    )
    ctx.call_oi_wall = 24030.0
    tp1 = _structural_tp1(ctx, Direction.LONG, 24000.0, 20.0, 2.0)
    assert tp1 == 24030.0


def test_wall_counts_as_confluence_for_index() -> None:
    eng = IndiaSignalScoringEngine()
    from tests.signal_factory import make_signal

    sig = make_signal(base="NIFTY", entry=24000.0)
    # Put a wall exactly at entry so it lands inside the 0.5*ATR tolerance.
    ctx_no = make_context(base="NIFTY", prev_day_high=0, prev_day_low=0,
                          prev_day_close=0, key_levels_extra=[])
    ctx_yes = make_context(base="NIFTY", prev_day_high=0, prev_day_low=0,
                           prev_day_close=0, key_levels_extra=[])
    ctx_yes.put_oi_wall = 24000.0
    assert eng._score_level_confluence(sig, ctx_yes) >= (
        eng._score_level_confluence(sig, ctx_no)
    )
