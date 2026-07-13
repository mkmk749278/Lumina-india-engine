"""Market-context stamp round-trips through india_signals into the API view.

Proves the Phase-1 stamp is consumed, not a scaffold: the columns the scanner
writes come back on the joined signal row that /api/signals, the ops Strategy
view, and the CSV download all read.
"""

from __future__ import annotations

import pytest

from src.db import close_db
from src.signal_store import (
    get_signal_by_id,
    get_signals,
    init_tables,
    insert_signal,
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


async def test_market_context_columns_round_trip() -> None:
    sig = _make_signal("SHORT")
    sig.market_direction = "LONG_BIASED"
    sig.session_phase = "MIDDAY_CHOP"
    sig.vix_regime = "LOW"
    await insert_signal(sig)

    row = await get_signal_by_id(sig.signal_id)
    assert row is not None
    # A SHORT signal stamped into a LONG-biased midday-chop tape — the exact
    # cohort that bled on 2026-07-13, now sliceable straight from the row.
    assert row["market_direction"] == "LONG_BIASED"
    assert row["session_phase"] == "MIDDAY_CHOP"
    assert row["vix_regime"] == "LOW"

    listed = await get_signals(limit=10)
    assert listed and listed[0]["session_phase"] == "MIDDAY_CHOP"


async def test_pre_context_signal_defaults_are_empty() -> None:
    sig = _make_signal("LONG")  # nothing stamped (legacy path)
    await insert_signal(sig)
    row = await get_signal_by_id(sig.signal_id)
    assert row is not None
    assert row["market_direction"] == ""
    assert row["vix_regime"] == ""
