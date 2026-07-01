"""Evaluators: each detects one setup and emits a candidate IndiaSignal."""

from __future__ import annotations

from src.channels.base import Evaluator
from src.channels.india_scalp import (
    BreakdownShort,
    DivergenceContinuation,
    ExpiryGammaSqueeze,
    FailedAuctionReclaim,
    IndiaVixExtreme,
    LiquiditySweepReversal,
    MaCrossTrendShift,
    OiSpikeReversal,
    OpeningRangeBreakout,
    PcrExtreme,
    QuietCompressionBreak,
    SrFlipRetest,
    TrendPullbackEma,
    VolumeSurgeBreakout,
)

__all__ = [
    "BreakdownShort",
    "DivergenceContinuation",
    "Evaluator",
    "ExpiryGammaSqueeze",
    "FailedAuctionReclaim",
    "IndiaVixExtreme",
    "LiquiditySweepReversal",
    "MaCrossTrendShift",
    "OiSpikeReversal",
    "OpeningRangeBreakout",
    "PcrExtreme",
    "QuietCompressionBreak",
    "SrFlipRetest",
    "TrendPullbackEma",
    "VolumeSurgeBreakout",
]
