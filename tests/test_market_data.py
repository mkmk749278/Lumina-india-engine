"""IndiaMarketData — VIX tracking, max-pain computation."""

from __future__ import annotations

from src.data.india_market_data import IndiaMarketData


# --- VIX ---

def test_vix_update_and_read() -> None:
    md = IndiaMarketData()
    assert md.get_vix() == 0.0
    md.update_vix(18.5)
    assert md.get_vix() == 18.5

def test_vix_latest_wins() -> None:
    md = IndiaMarketData()
    md.update_vix(15.0)
    md.update_vix(22.0)
    assert md.get_vix() == 22.0


# --- max-pain ---

def test_max_pain_manual_set() -> None:
    md = IndiaMarketData()
    md.update_max_pain("NIFTY", 24000.0)
    assert md.get_max_pain("NIFTY") == 24000.0
    assert md.get_max_pain("BANKNIFTY") is None

def test_max_pain_computation() -> None:
    md = IndiaMarketData()
    strikes = [23900.0, 23950.0, 24000.0, 24050.0, 24100.0]
    call_oi = [1000.0, 2000.0, 5000.0, 3000.0, 1500.0]
    put_oi = [1500.0, 3000.0, 4000.0, 2000.0, 500.0]

    result = md.compute_and_set_max_pain("NIFTY", strikes, call_oi, put_oi)
    assert result in strikes
    assert md.get_max_pain("NIFTY") == result

def test_max_pain_at_heavy_oi_strike() -> None:
    md = IndiaMarketData()
    strikes = [23000.0, 24000.0, 25000.0]
    call_oi = [0.0, 10000.0, 0.0]
    put_oi = [0.0, 10000.0, 0.0]

    result = md.compute_and_set_max_pain("NIFTY", strikes, call_oi, put_oi)
    assert result == 24000.0

def test_max_pain_empty_chain() -> None:
    md = IndiaMarketData()
    result = md.compute_and_set_max_pain("NIFTY", [], [], [])
    assert result == 0.0
