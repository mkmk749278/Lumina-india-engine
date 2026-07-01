"""OI (Open Interest) tracking and PCR computation.

Fed by a 1-minute poller (Fyers REST) that calls ``update_oi`` with fresh
data.  The scanner reads ``get_oi_change_15m_pct``, ``get_current_oi``,
and the PCR extreme flags.

CLAUDE.md cost discipline: entirely in-memory.  The poller is the only
network call, capped at 1/min — well below any cost concern.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class OISnapshot:
    ts: datetime
    oi: float


class IndiaOIStore:
    """Per-symbol OI history + market-wide PCR."""

    def __init__(
        self,
        pcr_extreme_low: float = 0.7,
        pcr_extreme_high: float = 1.3,
        max_snapshots: int = 400,
    ) -> None:
        self._pcr_low = pcr_extreme_low
        self._pcr_high = pcr_extreme_high
        self._max = max_snapshots

        self._oi: dict[str, deque[OISnapshot]] = {}
        self._pcr: float = 0.0
        self._total_put_oi: float = 0.0
        self._total_call_oi: float = 0.0

    # ------------------------------------------------------------------
    # Writers (called by the 1-minute poller)
    # ------------------------------------------------------------------

    def update_oi(self, symbol: str, oi: float, ts: datetime) -> None:
        """Record an OI snapshot for a futures symbol."""
        if symbol not in self._oi:
            self._oi[symbol] = deque(maxlen=self._max)
        self._oi[symbol].append(OISnapshot(ts=ts, oi=oi))

    def update_pcr(
        self, total_put_oi: float, total_call_oi: float
    ) -> None:
        """Update market-wide PCR from option chain aggregates."""
        self._total_put_oi = total_put_oi
        self._total_call_oi = total_call_oi
        self._pcr = (
            total_put_oi / total_call_oi if total_call_oi > 0 else 0.0
        )

    # ------------------------------------------------------------------
    # Readers (consumed by context builder)
    # ------------------------------------------------------------------

    def get_current_oi(self, symbol: str) -> float:
        snaps = self._oi.get(symbol)
        if not snaps:
            return 0.0
        return snaps[-1].oi

    def get_oi_change_15m_pct(self, symbol: str) -> float:
        """OI change over the last ~15 minutes as a percentage."""
        snaps = self._oi.get(symbol)
        if not snaps or len(snaps) < 2:
            return 0.0
        latest = snaps[-1]
        target_ts = latest.ts - timedelta(minutes=15)
        baseline = snaps[0]
        for s in snaps:
            if s.ts <= target_ts:
                baseline = s
            else:
                break
        if baseline.oi == 0.0:
            return 0.0
        return ((latest.oi - baseline.oi) / baseline.oi) * 100.0

    def get_pcr(self) -> float:
        return self._pcr

    def is_pcr_extreme_bearish(self) -> bool:
        return self._pcr > 0 and self._pcr < self._pcr_low

    def is_pcr_extreme_bullish(self) -> bool:
        return self._pcr > self._pcr_high
