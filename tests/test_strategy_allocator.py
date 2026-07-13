"""strategy_allocator — recommendation-mode verdicts over the edge matrix."""

from __future__ import annotations

import pytest

from src.db import close_db
from src.signal_store import init_tables, insert_outcome, insert_signal
from src.strategy_allocator import Verdict, build_allocation, get_allocation, recommend
from tests.test_trade_monitor import _make_signal


def _cell(key, n, ev, win=50.0):
    return {"key": key, "n": n, "ev_net_pct": ev, "win_rate": win}


_TH = {"min_sample": 20, "ev_floor": 0.0, "suppress_ev": -0.05}


def test_verdicts_by_expectancy_and_sample():
    cells = [
        _cell("A/LONG", 40, 0.30),   # clearly +EV
        _cell("B/SHORT", 40, -0.20),  # clearly -EV
        _cell("C/LONG", 40, -0.02),   # marginal (between suppress and floor)
        _cell("D/SHORT", 5, 0.50),    # thin — not judged
    ]
    recs = {r["key"]: r for r in recommend(cells, **_TH)}
    assert recs["A/LONG"]["verdict"] == Verdict.EMIT
    assert recs["B/SHORT"]["verdict"] == Verdict.SUPPRESS
    assert recs["C/LONG"]["verdict"] == Verdict.HOLD
    assert recs["D/SHORT"]["verdict"] == Verdict.INSUFFICIENT_DATA


def test_recommendations_are_best_first():
    cells = [_cell("lo", 40, -0.1), _cell("hi", 40, 0.4), _cell("mid", 40, 0.1)]
    keys = [r["key"] for r in recommend(cells, **_TH)]
    assert keys == ["hi", "mid", "lo"]


def test_build_allocation_tallies_verdicts():
    matrix = {
        "by_setup_direction": [_cell("VSB/LONG", 40, 0.3), _cell("SRF/SHORT", 40, -0.2)],
        "by_market_vs_signal": [_cell("LONG_BIASED/SHORT", 40, -0.3)],
        "by_setup": [_cell("VSB", 40, 0.3)],
    }
    alloc = build_allocation(matrix)
    # Thresholds come from config defaults here; both -EV cells suppress.
    assert alloc["tally"].get("SUPPRESS", 0) >= 2
    assert alloc["tally"].get("EMIT", 0) >= 2


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


async def test_get_allocation_is_recommendation_mode():
    from datetime import datetime

    import config

    now = datetime.now(config.IST)
    sig = _make_signal("LONG")
    sig.market_direction = "LONG_BIASED"
    sig.session_phase = "POWER_HOUR"
    await insert_signal(sig)
    await insert_outcome(sig.signal_id, "TP1_HIT", sig.tp1, 10.0, 0.4, now)

    result = await get_allocation(days=30)
    assert result["mode"] == "recommendation"  # observe-only
    assert result["sample"] == 1
    assert "by_setup_direction" in result["allocation"]["recommendations"]
    # One resolved trade is below MIN_SAMPLE → judged INSUFFICIENT_DATA.
    setup_recs = result["allocation"]["recommendations"]["by_setup_direction"]
    assert setup_recs[0]["verdict"] == Verdict.INSUFFICIENT_DATA
