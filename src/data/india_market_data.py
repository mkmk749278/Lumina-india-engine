"""India VIX and market-wide data.

``india_vix`` is updated from the Fyers WebSocket (INDIA VIX is a
subscribable symbol: ``NSE:INDIAVIX-INDEX``).  ``max_pain_strike``
is computed from option chain OI data polled via REST.

CLAUDE.md cost discipline: in-memory only.
"""

from __future__ import annotations

import time

import config


class IndiaMarketData:
    """VIX + max-pain tracking for the scanner context."""

    def __init__(self) -> None:
        self._vix: float = 0.0
        # Monotonic clock of the last VIX update. A VIX whose feed silently
        # died must read as *unavailable* (0.0 — consumers already fail safe:
        # no low-VIX scoring bonus, no event-risk trip, VIX-extreme cannot
        # arm), not as its last observation — a 3-hour-old 24.9 sitting just
        # under the event threshold while real VIX spikes is exactly the
        # silent lie the tick-freshness layer exists to prevent.
        self._vix_mono: float | None = None
        self._max_pain: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def update_vix(self, vix: float) -> None:
        self._vix = vix
        self._vix_mono = time.monotonic()

    def update_max_pain(self, base: str, strike: float) -> None:
        """Set the computed max-pain strike for an index base."""
        self._max_pain[base] = strike

    def compute_and_set_max_pain(
        self,
        base: str,
        strikes: list[float],
        call_oi: list[float],
        put_oi: list[float],
    ) -> float:
        """Compute max-pain from option chain OI and store it.

        Max-pain is the strike at which the total intrinsic value of all
        outstanding options is minimised (i.e. where option sellers — market
        makers — lose the least money at expiry).
        """
        if (
            not strikes
            or len(strikes) != len(call_oi)
            or len(strikes) != len(put_oi)
        ):
            return 0.0

        min_pain = float("inf")
        best_strike = 0.0

        # At an expiry price P: calls struck below P pay (P - strike), puts
        # struck above P pay (strike - P). Max pain minimises that total
        # payout to holders.
        for candidate in strikes:
            pain = 0.0
            for i, s in enumerate(strikes):
                if candidate > s:
                    pain += call_oi[i] * (candidate - s)
                elif candidate < s:
                    pain += put_oi[i] * (s - candidate)
            if pain < min_pain:
                min_pain = pain
                best_strike = candidate

        self._max_pain[base] = best_strike
        return best_strike

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def get_vix(self) -> float:
        """Last VIX, or 0.0 (= unavailable) once the reading has gone stale."""
        if self._vix_mono is None:
            return 0.0
        if time.monotonic() - self._vix_mono > config.VIX_TTL_SEC:
            return 0.0
        return self._vix

    def get_max_pain(self, base: str) -> float | None:
        return self._max_pain.get(base)
