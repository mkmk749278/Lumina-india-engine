"""_structural_tp1 (G2) — snap the fixed-R breakout TP1 to nearest real level
in the [MIN_RR, fallback_rr] band; else keep the exact R-multiple fallback.

Prices are realistic NIFTY levels (50-pt round grid) so the round-number
candidates never fall inside the tight test bands by accident.
"""

from __future__ import annotations

import config
from src.channels.india_scalp import _structural_tp1
from src.signals.model import Direction
from tests.signal_factory import make_context

# entry 24000, sl_dist 20 -> 2R fallback = 24040 (LONG) / 23960 (SHORT).
# band = [1.2R, 2R] = [24, 40] pts from entry.
ENTRY = 24000.0
SL_DIST = 20.0
RR = 2.0


def _ctx(**kw):
    kw.setdefault("prev_day_high", 0.0)
    kw.setdefault("prev_day_low", 0.0)
    kw.setdefault("prev_day_close", 0.0)
    kw.setdefault("key_levels_extra", [])
    return make_context(base="NIFTY", **kw)


def test_snaps_long_tp1_to_nearest_level_in_band() -> None:
    # PDH 24030 = 1.5R, inside the band and the nearest qualifying level.
    tp1 = _structural_tp1(_ctx(prev_day_high=24030.0), Direction.LONG, ENTRY, SL_DIST, RR)
    assert tp1 == 24030.0


def test_short_snaps_below_entry() -> None:
    tp1 = _structural_tp1(_ctx(prev_day_low=23970.0), Direction.SHORT, ENTRY, SL_DIST, RR)
    assert tp1 == 23970.0


def test_falls_back_when_level_beyond_band() -> None:
    # PDH 24200 = 10R, outside [1.2R, 2R] -> 2R fallback.
    tp1 = _structural_tp1(_ctx(prev_day_high=24200.0), Direction.LONG, ENTRY, SL_DIST, RR)
    assert tp1 == 24040.0


def test_ignores_level_below_min_rr() -> None:
    # PDH 24010 = 0.5R (< 1.2R) — must not shrink TP1 below the cost floor.
    tp1 = _structural_tp1(_ctx(prev_day_high=24010.0), Direction.LONG, ENTRY, SL_DIST, RR)
    assert tp1 == 24040.0


def test_picks_nearest_of_several() -> None:
    ctx = _ctx(prev_day_high=24035.0, prev_day_close=24028.0, key_levels_extra=[24039.0])
    tp1 = _structural_tp1(ctx, Direction.LONG, ENTRY, SL_DIST, RR)
    assert tp1 == 24028.0  # nearest to entry among the in-band levels


def test_never_moves_past_fallback_distance() -> None:
    tp1 = _structural_tp1(_ctx(prev_day_high=24035.0), Direction.LONG, ENTRY, SL_DIST, RR)
    assert ENTRY < tp1 <= 24040.0


def test_disabled_flag_is_exact_fallback(monkeypatch) -> None:
    monkeypatch.setattr(config, "STRUCTURAL_TP1_ENABLED", False)
    tp1 = _structural_tp1(_ctx(prev_day_high=24030.0), Direction.LONG, ENTRY, SL_DIST, RR)
    assert tp1 == 24040.0


def test_degenerate_geometry_returns_fallback() -> None:
    assert _structural_tp1(_ctx(), Direction.LONG, 0.0, SL_DIST, RR) == SL_DIST * RR
    assert _structural_tp1(_ctx(), Direction.LONG, ENTRY, 0.0, RR) == ENTRY
