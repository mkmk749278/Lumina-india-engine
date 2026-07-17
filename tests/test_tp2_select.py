"""TP2 selection mode (Session 21) — target-anchored vs legacy nearest.

The live ledger showed the legacy nearest-to-entry pick pinning mapped TP2s
to the bottom of the 1.5-3.0x band, capping TP2_HIT below a marked-to-close
TP1_EXPIRED. target_anchored picks the level nearest the R-multiple anchor.
"""

from __future__ import annotations

import config
from src.channels.india_scalp import _derive_tp2
from src.regime import Regime
from src.signals.model import Direction, IndiaContext


def _ctx(levels: list[float]) -> IndiaContext:
    return IndiaContext(
        base="NIFTY",
        regime_60m=Regime.TRENDING_UP,
        regime_daily=Regime.TRENDING_UP,
        candles_5m=[],
        volume_avg_5m_20=0.0,
        atr14_5m=10.0,
        prev_day_high=levels[0] if levels else 0.0,
        prev_day_low=levels[1] if len(levels) > 1 else 0.0,
        prev_day_close=levels[2] if len(levels) > 2 else 0.0,
        oi_change_15m_pct=0.0,
        india_vix=13.0,
    )


def test_target_anchored_picks_level_near_r_multiple() -> None:
    # entry 100, tp1 110 (dist 10) → band [115, 130], anchor 120.
    # Levels at 115.5 (band bottom) and 121 (near anchor).
    ctx = _ctx([115.5, 121.0, 0.0])
    tp2 = _derive_tp2(ctx, 100.0, 110.0, Direction.LONG)
    assert tp2 == 121.0


def test_legacy_nearest_mode_picks_band_bottom(monkeypatch) -> None:
    monkeypatch.setattr(config, "TP2_SELECT_MODE", "nearest")
    ctx = _ctx([115.5, 121.0, 0.0])
    tp2 = _derive_tp2(ctx, 100.0, 110.0, Direction.LONG)
    assert tp2 == 115.5


def test_fallback_is_r_multiple_when_no_mapped_level() -> None:
    ctx = _ctx([500.0, 1.0, 0.0])  # nothing inside the band
    tp2 = _derive_tp2(ctx, 100.0, 110.0, Direction.LONG)
    assert tp2 == 100.0 + 10.0 * config.TP2_DIST_MULT


def test_short_mirrors_anchor() -> None:
    # entry 100, tp1 90 (dist 10) → band [70, 85] below, anchor 80.
    ctx = _ctx([84.5, 79.0, 0.0])
    tp2 = _derive_tp2(ctx, 100.0, 90.0, Direction.SHORT)
    assert tp2 == 79.0
