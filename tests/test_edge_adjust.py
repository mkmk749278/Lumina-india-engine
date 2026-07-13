"""Edge-aware confidence adjustment — bounded, sample-gated, non-overfit."""

from __future__ import annotations

import config
from src.signal_quality import IndiaSignalScoringEngine
from src.strategy_edge import build_edge_index
from tests.signal_factory import make_signal


def _engine(index):
    eng = IndiaSignalScoringEngine()
    eng.set_edge_index(index)
    return eng


def test_no_index_means_no_adjustment():
    eng = IndiaSignalScoringEngine()
    sig = make_signal()
    assert eng._score_measured_edge(sig) == 0.0


def test_thin_cohort_is_inert(monkeypatch):
    # A cohort below ALLOCATOR_MIN_SAMPLE must not move the score (no overfit).
    monkeypatch.setattr(config, "ALLOCATOR_MIN_SAMPLE", 20)
    sig = make_signal()
    eng = _engine({(sig.setup_class, sig.direction): {"n": 5, "ev_net_pct": 0.5}})
    assert eng._score_measured_edge(sig) == 0.0


def test_positive_edge_nudges_up_capped(monkeypatch):
    monkeypatch.setattr(config, "ALLOCATOR_MIN_SAMPLE", 20)
    monkeypatch.setattr(config, "EDGE_ADJUST_K", 20.0)
    monkeypatch.setattr(config, "EDGE_ADJUST_CAP", 8.0)
    sig = make_signal()
    # ev +0.3% * K 20 = +6 (under the ±8 cap)
    eng = _engine({(sig.setup_class, sig.direction): {"n": 40, "ev_net_pct": 0.3}})
    assert eng._score_measured_edge(sig) == 6.0
    # ev +1.0% * 20 = +20 → capped at +8
    eng2 = _engine({(sig.setup_class, sig.direction): {"n": 40, "ev_net_pct": 1.0}})
    assert eng2._score_measured_edge(sig) == 8.0


def test_negative_edge_nudges_down(monkeypatch):
    monkeypatch.setattr(config, "ALLOCATOR_MIN_SAMPLE", 20)
    sig = make_signal()
    eng = _engine({(sig.setup_class, sig.direction): {"n": 40, "ev_net_pct": -0.5}})
    assert eng._score_measured_edge(sig) == -8.0  # -10 capped to -8


def test_disabled_flag_restores_prior_scoring(monkeypatch):
    monkeypatch.setattr(config, "EDGE_ADJUST_ENABLED", False)
    sig = make_signal()
    eng = _engine({(sig.setup_class, sig.direction): {"n": 40, "ev_net_pct": 0.5}})
    assert eng._score_measured_edge(sig) == 0.0


def test_build_edge_index_keys_by_setup_and_direction():
    rows = [
        {"setup_class": "VSB", "direction": "LONG", "outcome": "TP1_HIT", "pct": 0.4}
        for _ in range(3)
    ]
    index = build_edge_index(rows)
    assert ("VSB", "LONG") in index
    assert index[("VSB", "LONG")]["n"] == 3
