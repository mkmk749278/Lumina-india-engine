"""Evaluators: each detects one setup and emits a candidate IndiaSignal."""

from __future__ import annotations

from src.channels.base import Evaluator
from src.channels.india_scalp import LiquiditySweepReversal

__all__ = ["Evaluator", "LiquiditySweepReversal"]
