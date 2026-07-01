"""Evaluators: each detects one setup and emits a candidate IndiaSignal."""

from __future__ import annotations

from src.channels.base import Evaluator
from src.channels.india_scalp import (
    BreakdownShort,
    IndiaVixExtreme,
    LiquiditySweepReversal,
    OiSpikeReversal,
    OpeningRangeBreakout,
    PcrExtreme,
    TrendPullbackEma,
    VolumeSurgeBreakout,
)

__all__ = [
    "BreakdownShort",
    "Evaluator",
    "IndiaVixExtreme",
    "LiquiditySweepReversal",
    "OiSpikeReversal",
    "OpeningRangeBreakout",
    "PcrExtreme",
    "TrendPullbackEma",
    "VolumeSurgeBreakout",
]
