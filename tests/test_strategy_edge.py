"""Strategy×Context edge matrix (Phase 3) — aggregation + DB round-trip."""

from __future__ import annotations

import pytest

import config
from src.db import close_db
from src.signal_store import (
    get_resolved_signals,
    init_tables,
    insert_outcome,
    insert_signal,
)
from src.strategy_edge import _is_win, build_edge_matrix, get_edge_matrix
from tests.test_trade_monitor import _make_signal


def _row(setup, direction, market_dir, phase, outcome, pct, tier="B"):
    return {
        "setup_class": setup,
        "direction": direction,
        "tier": tier,
        "session_phase": phase,
        "market_direction": market_dir,
        "vix_regime": "LOW",
        "outcome": outcome,
        "pct": pct,
    }


# --- win convention -------------------------------------------------------

def test_tp1_banked_outcomes_all_count_as_wins():
    assert _is_win("TP1_HIT")
    assert _is_win("TP1_BE")
    assert _is_win("TP2_HIT")
    assert _is_win("TP1_EXPIRED")
    assert not _is_win("SL_HIT")
    assert not _is_win("EXPIRED")  # neither leg touched — not a win


# --- aggregation math -----------------------------------------------------

def test_direction_cohort_matches_the_0713_pattern():
    rows = [
        _row("VSB", "LONG", "LONG_BIASED", "POWER_HOUR", "TP1_HIT", 0.40),
        _row("VSB", "LONG", "LONG_BIASED", "MIDDAY_CHOP", "TP1_HIT", 0.40),
        _row("BDS", "SHORT", "LONG_BIASED", "POWER_HOUR", "SL_HIT", -0.20),
        _row("SRF", "SHORT", "LONG_BIASED", "MIDDAY_CHOP", "SL_HIT", -0.20),
    ]
    m = build_edge_matrix(rows)

    overall = m["overall"][0]
    assert overall["n"] == 4 and overall["wins"] == 2 and overall["win_rate"] == 50.0

    cohorts = {c["key"]: c for c in m["by_market_vs_signal"]}
    longs = cohorts["LONG_BIASED/LONG"]
    shorts = cohorts["LONG_BIASED/SHORT"]
    assert longs["win_rate"] == 100.0 and longs["net_pct"] == 0.8
    assert shorts["win_rate"] == 0.0 and shorts["losses"] == 2
    # Cost-adjusted expectancy: longs +0.40 − cost > 0 > shorts.
    assert longs["ev_net_pct"] > 0 > shorts["ev_net_pct"]
    # Best expectancy first.
    assert m["by_market_vs_signal"][0]["key"] == "LONG_BIASED/LONG"


def test_expired_counts_toward_n_but_not_wins():
    rows = [
        _row("VSB", "LONG", "NEUTRAL", "CLOSING", "TP1_HIT", 0.5),
        _row("VSB", "LONG", "NEUTRAL", "CLOSING", "EXPIRED", -0.1),
    ]
    cell = build_edge_matrix(rows)["by_setup"][0]
    assert cell["n"] == 2 and cell["wins"] == 1 and cell["expired"] == 1
    assert cell["win_rate"] == 50.0


def test_empty_rows_give_empty_dimensions():
    m = build_edge_matrix([])
    assert m["overall"] == [] and m["by_setup"] == []


# --- DB round-trip --------------------------------------------------------

@pytest.fixture(autouse=True)
async def _setup_db(tmp_path, monkeypatch):
    monkeypatch.setenv("INDIA_DATA_DIR", str(tmp_path))
    import src.db

    src.db._conn = None
    src.db._DB_DIR = tmp_path
    src.db._DB_PATH = tmp_path / "india_db.sqlite3"
    await init_tables()
    yield
    await close_db()


async def test_get_edge_matrix_reads_stored_outcomes():
    from datetime import datetime

    now = datetime.now(config.IST)
    win = _make_signal("LONG")
    win.session_phase = "POWER_HOUR"
    win.market_direction = "LONG_BIASED"
    win.vix_regime = "LOW"
    await insert_signal(win)
    await insert_outcome(win.signal_id, "TP1_HIT", win.tp1, 10.0, 0.4, now)

    loss = _make_signal("SHORT")
    loss.session_phase = "MIDDAY_CHOP"
    loss.market_direction = "LONG_BIASED"
    loss.vix_regime = "LOW"
    await insert_signal(loss)
    await insert_outcome(loss.signal_id, "SL_HIT", loss.sl, -5.0, -0.2, now)

    assert len(await get_resolved_signals(days=30)) == 2

    result = await get_edge_matrix(days=30)
    assert result["sample"] == 2
    cohorts = {c["key"]: c for c in result["matrix"]["by_market_vs_signal"]}
    assert cohorts["LONG_BIASED/LONG"]["win_rate"] == 100.0
    assert cohorts["LONG_BIASED/SHORT"]["win_rate"] == 0.0
