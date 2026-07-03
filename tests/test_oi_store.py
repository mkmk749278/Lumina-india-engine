"""IndiaOIStore — OI tracking, PCR computation, extreme flags."""

from __future__ import annotations

from datetime import datetime, timedelta

import config
from src.data.india_oi_store import IndiaOIStore

IST = config.IST
_SYM = "NSE:NIFTY26JULFUT"


_BASE_DT = IST.localize(datetime(2026, 7, 7, 0, 0, 0))


def _ist(h: int, m: int) -> datetime:
    return _BASE_DT + timedelta(hours=h, minutes=m)


# --- OI tracking ---

def test_current_oi_returns_latest() -> None:
    store = IndiaOIStore()
    store.update_oi(_SYM, 5_000_000.0, _ist(10, 0))
    store.update_oi(_SYM, 5_200_000.0, _ist(10, 1))
    assert store.get_current_oi(_SYM) == 5_200_000.0

def test_current_oi_zero_when_empty() -> None:
    store = IndiaOIStore()
    assert store.get_current_oi(_SYM) == 0.0


# --- OI change 15m ---

def test_oi_change_15m_positive() -> None:
    store = IndiaOIStore()
    base = _ist(10, 0)
    store.update_oi(_SYM, 5_000_000.0, base)
    for i in range(1, 16):
        store.update_oi(_SYM, 5_000_000.0 + i * 10_000, base + timedelta(minutes=i))
    pct = store.get_oi_change_15m_pct(_SYM)
    assert pct > 0.0

def test_oi_change_15m_negative() -> None:
    store = IndiaOIStore()
    base = _ist(10, 0)
    store.update_oi(_SYM, 5_000_000.0, base)
    for i in range(1, 16):
        store.update_oi(_SYM, 5_000_000.0 - i * 10_000, base + timedelta(minutes=i))
    pct = store.get_oi_change_15m_pct(_SYM)
    assert pct < 0.0

def test_oi_change_zero_with_single_snapshot() -> None:
    store = IndiaOIStore()
    store.update_oi(_SYM, 5_000_000.0, _ist(10, 0))
    assert store.get_oi_change_15m_pct(_SYM) == 0.0


# --- PCR ---

def test_pcr_computation() -> None:
    store = IndiaOIStore()
    store.update_pcr(total_put_oi=700_000.0, total_call_oi=1_000_000.0)
    assert abs(store.get_pcr() - 0.7) < 0.001

def test_pcr_zero_when_no_calls() -> None:
    store = IndiaOIStore()
    store.update_pcr(total_put_oi=700_000.0, total_call_oi=0.0)
    assert store.get_pcr() == 0.0


# --- PCR extremes ---

def test_pcr_extreme_bearish() -> None:
    store = IndiaOIStore(pcr_extreme_low=0.7)
    store.update_pcr(total_put_oi=500_000.0, total_call_oi=1_000_000.0)
    assert store.is_pcr_extreme_bearish()
    assert not store.is_pcr_extreme_bullish()

def test_pcr_extreme_bullish() -> None:
    store = IndiaOIStore(pcr_extreme_high=1.3)
    store.update_pcr(total_put_oi=1_500_000.0, total_call_oi=1_000_000.0)
    assert store.is_pcr_extreme_bullish()
    assert not store.is_pcr_extreme_bearish()

def test_pcr_normal_range() -> None:
    store = IndiaOIStore()
    store.update_pcr(total_put_oi=900_000.0, total_call_oi=1_000_000.0)
    assert not store.is_pcr_extreme_bearish()
    assert not store.is_pcr_extreme_bullish()
