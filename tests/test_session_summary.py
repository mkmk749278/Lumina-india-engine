"""Session summary — aggregation of signals, suppressions, outcomes."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

import config
from src.db import close_db
from src.signal_store import (
    get_session_summaries,
    init_tables,
    insert_outcome,
    insert_signal,
    insert_suppression,
    write_session_summary,
)
from tests.test_trade_monitor import _make_signal


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


async def test_summary_aggregates_all_three_tables() -> None:
    now = datetime.now(config.IST)

    long_sig = _make_signal("LONG")
    long_sig.confidence = 82.0
    long_sig.tier = "A+"
    await insert_signal(long_sig)

    short_sig = _make_signal("SHORT")
    short_sig.confidence = 70.0
    short_sig.tier = "B"
    await insert_signal(short_sig)

    await insert_suppression(
        "NIFTY", "cooldown_gate", "fired 10s ago", "ORB", "LONG", now
    )
    await insert_suppression(
        "BANKNIFTY", "cooldown_gate", "fired 20s ago", "ORB", "SHORT", now
    )
    await insert_suppression(
        "NIFTY", "min_atr_gate", "ATR 1.0 < 3.0", "TPE", "LONG", now
    )

    await insert_outcome("sig-LONG", "TP1_HIT", 24600.0, 100.0, 0.4, now)
    await insert_outcome("sig-SHORT", "SL_HIT", 24550.0, -50.0, -0.2, now)

    summary = await write_session_summary()

    assert summary["signal_count"] == 2
    assert summary["a_plus_count"] == 1
    assert summary["b_count"] == 1
    assert summary["avg_confidence"] == 76.0
    assert summary["total_suppressed"] == 3
    assert json.loads(summary["gates_fired"]) == {
        "cooldown_gate": 2,
        "min_atr_gate": 1,
    }
    assert summary["tp1_count"] == 1
    assert summary["sl_count"] == 1
    assert summary["expired_count"] == 0
    assert summary["total_points"] == 50.0
    # % is the cross-instrument-comparable measure: +0.4% and -0.2% -> +0.2%
    # cumulative, +0.1% average per signal.
    assert summary["total_pct"] == 0.2
    assert summary["avg_pct"] == 0.1


async def test_summary_idempotent_per_date() -> None:
    await write_session_summary()
    await write_session_summary()  # rewrite, not duplicate
    rows = await get_session_summaries()
    assert len(rows) == 1
    assert rows[0]["signal_count"] == 0


async def test_empty_day_summary_is_zeroes() -> None:
    summary = await write_session_summary()
    assert summary["signal_count"] == 0
    assert summary["total_points"] == 0
    assert json.loads(summary["gates_fired"]) == {}
