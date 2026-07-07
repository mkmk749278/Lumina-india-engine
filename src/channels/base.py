"""Evaluator interface.

Every evaluator detects exactly one setup and returns a candidate ``IndiaSignal``
(with its own SL/TP geometry — CLAUDE.md) or ``None``. The scanner runs each
enabled evaluator per instrument per scan, then gates + scores the candidates.
Evaluators are pure functions of the context: no I/O, no shared mutable state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.signals.model import IndiaContext, IndiaSignal


class Evaluator(ABC):
    """Base class for all setup evaluators."""

    #: Stringly-coupled to scoring affinity + telemetry (SetupClass value).
    setup_class: str
    #: Feature flag; the scanner skips disabled evaluators.
    enabled: bool = True
    #: Index-only setups (market-wide PCR / index max-pain) — the scanner skips
    #: them for stock bases, which have no equivalent market-wide inputs.
    index_only: bool = False

    @abstractmethod
    def evaluate(self, ctx: IndiaContext) -> IndiaSignal | None:
        """Return a candidate signal for this setup, or ``None`` if not present."""
        raise NotImplementedError
