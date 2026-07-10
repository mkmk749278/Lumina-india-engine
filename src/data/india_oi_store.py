"""OI (Open Interest) tracking and PCR computation.

Fed by a 1-minute poller (Fyers REST) that calls ``update_oi`` with fresh
data.  The scanner reads ``get_oi_change_15m_pct``, ``get_current_oi``,
and the PCR extreme flags.

CLAUDE.md cost discipline: entirely in-memory.  The poller is the only
network call, capped at 1/min — well below any cost concern.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

import config


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
        # Monotonic clock of the last successful chain poll — stale PCR
        # reads neutral, same freshness doctrine as VIX/ticks.
        self._pcr_mono: float | None = None
        # Per-base option-chain OI totals; market-wide PCR is computed over
        # the sum so alternating NIFTY/BANKNIFTY chain polls don't make the
        # ratio flip between two different per-index values.
        self._put_oi_by_base: dict[str, float] = {}
        self._call_oi_by_base: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Writers (called by the 1-minute poller)
    # ------------------------------------------------------------------

    def update_oi(self, symbol: str, oi: float, ts: datetime) -> None:
        """Record an OI snapshot for a futures symbol."""
        if symbol not in self._oi:
            self._oi[symbol] = deque(maxlen=self._max)
        self._oi[symbol].append(OISnapshot(ts=ts, oi=oi))

    def update_pcr(
        self, total_put_oi: float, total_call_oi: float, base: str = "MARKET"
    ) -> None:
        """Update option-chain OI aggregates for one index *base*."""
        self._put_oi_by_base[base] = total_put_oi
        self._call_oi_by_base[base] = total_call_oi
        self._pcr_mono = time.monotonic()

    # ------------------------------------------------------------------
    # Readers (consumed by context builder)
    # ------------------------------------------------------------------

    @staticmethod
    def _fresh(snap: OISnapshot) -> bool:
        """The OI poller stopped delivering -> the value is unavailable, not
        its last observation (consumers fail safe on 0.0)."""
        age = datetime.now(snap.ts.tzinfo) - snap.ts
        return age <= timedelta(seconds=config.OI_TTL_SEC)

    def get_current_oi(self, symbol: str) -> float:
        snaps = self._oi.get(symbol)
        if not snaps or not self._fresh(snaps[-1]):
            return 0.0
        return snaps[-1].oi

    def get_oi_change_15m_pct(self, symbol: str) -> float:
        """OI change over the last ~15 minutes as a percentage."""
        snaps = self._oi.get(symbol)
        if not snaps or len(snaps) < 2 or not self._fresh(snaps[-1]):
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
        """Market-wide PCR over all polled index chains (0.0 if none yet or
        the chain poll has gone stale)."""
        if (
            self._pcr_mono is None
            or time.monotonic() - self._pcr_mono > config.PCR_TTL_SEC
        ):
            return 0.0
        total_calls = sum(self._call_oi_by_base.values())
        total_puts = sum(self._put_oi_by_base.values())
        return total_puts / total_calls if total_calls > 0 else 0.0

    def is_pcr_extreme_bearish(self) -> bool:
        pcr = self.get_pcr()
        return 0 < pcr < self._pcr_low

    def is_pcr_extreme_bullish(self) -> bool:
        return self.get_pcr() > self._pcr_high
