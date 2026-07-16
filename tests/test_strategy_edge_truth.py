"""Edge-matrix ledger-truth conventions (Session 21).

NOT_TRIGGERED exclusion, legacy single-target segregation, net-of-cost win
rate, the extension/duplicate dimensions, and NULL-context exclusion.
"""

from __future__ import annotations

import config
from src.strategy_edge import build_edge_matrix


def _row(
    outcome: str,
    pct: float,
    *,
    setup: str = "VOLUME_SURGE_BREAKOUT",
    direction: str = "LONG",
    market_dir: str | None = "NEUTRAL",
    phase: str | None = "MIDDAY_CHOP",
    vix: str | None = "LOW",
    tp2: float = 100.0,
    ext: float | None = 0.2,
    dup: int | None = 1,
) -> dict:
    return {
        "setup_class": setup,
        "direction": direction,
        "tier": "B",
        "session_phase": phase,
        "market_direction": market_dir,
        "vix_regime": vix,
        "outcome": outcome,
        "pct": pct,
        "tp2": tp2,
        "extension_vwap_atr": ext,
        "dup_index": dup,
    }


def test_not_triggered_excluded_from_n_but_counted() -> None:
    rows = [
        _row("TP1_BE", 0.2),
        _row("SL_HIT", -0.2),
        _row("NOT_TRIGGERED", 0.0),
        _row("NOT_TRIGGERED", 0.0),
    ]
    cell = build_edge_matrix(rows)["overall"][0]
    assert cell["n"] == 2
    assert cell["not_triggered"] == 2
    assert cell["win_rate"] == 50.0
    # avg over filled trades only — the zero-pct cancels must not dilute.
    assert cell["avg_pct"] == 0.0  # (0.2 - 0.2) / 2


def test_legacy_single_target_rows_are_counted_separately() -> None:
    rows = [
        _row("TP1_HIT", 0.5, tp2=0.0),  # legacy single-target
        _row("TP2_HIT", 0.4, tp2=105.0),
    ]
    cell = build_edge_matrix(rows)["overall"][0]
    assert cell["n"] == 2
    assert cell["legacy_n"] == 1


def test_win_rate_net_uses_cost_model() -> None:
    cost = config.ROUNDTRIP_COST_PCT
    rows = [
        _row("TP1_BE", cost + 0.01),  # beats cost
        _row("TP1_BE", cost - 0.01),  # TP1-banked but net loser
        _row("SL_HIT", -0.2),
    ]
    cell = build_edge_matrix(rows)["overall"][0]
    assert cell["win_rate"] == round(100.0 * 2 / 3, 1)
    assert cell["win_rate_net"] == round(100.0 * 1 / 3, 1)


def test_null_context_rows_excluded_from_context_dims_only() -> None:
    rows = [
        _row("TP1_BE", 0.2),
        _row("SL_HIT", -0.2, market_dir=None, phase=None, vix=None,
             ext=None, dup=None),
    ]
    m = build_edge_matrix(rows)
    # Overall/setup dims keep both rows.
    assert m["overall"][0]["n"] == 2
    # Context dims run over the stamped row only; the exclusion is reported.
    phase_cells = {c["key"]: c for c in m["by_session_phase"]}
    assert list(phase_cells) == ["MIDDAY_CHOP"]
    assert phase_cells["MIDDAY_CHOP"]["n"] == 1
    excluded = m["context_excluded"]
    assert excluded["by_session_phase"] == 1
    assert excluded["by_market_vs_signal"] == 1
    assert excluded["by_extension_bucket"] == 1


def test_extension_buckets_and_dup_dimension() -> None:
    rows = [
        _row("TP1_BE", 0.2, ext=-0.3, dup=1),
        _row("SL_HIT", -0.2, ext=0.9, dup=2),
        _row("SL_HIT", -0.2, ext=2.1, dup=2),
    ]
    m = build_edge_matrix(rows)
    ext_keys = {c["key"] for c in m["by_extension_bucket"]}
    assert ext_keys == {"BEHIND_VWAP", "EXTENDED(0.5-1.5)", "EXHAUSTED(>1.5)"}
    dup_cells = {c["key"]: c for c in m["by_dup_index"]}
    assert dup_cells["dup#1"]["n"] == 1
    assert dup_cells["dup#2"]["n"] == 2
