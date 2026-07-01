"""India VIX and market-wide data.

``india_vix`` is updated from the Fyers WebSocket (INDIA VIX is a
subscribable symbol: ``NSE:INDIAVIX-INDEX``).  ``max_pain_strike``
is computed from option chain OI data polled via REST.

CLAUDE.md cost discipline: in-memory only.
"""

from __future__ import annotations


class IndiaMarketData:
    """VIX + max-pain tracking for the scanner context."""

    def __init__(self) -> None:
        self._vix: float = 0.0
        self._max_pain: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------

    def update_vix(self, vix: float) -> None:
        self._vix = vix

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
        if not strikes or len(strikes) != len(call_oi) != len(put_oi):
            return 0.0

        min_pain = float("inf")
        best_strike = 0.0

        for candidate in strikes:
            pain = 0.0
            for i, s in enumerate(strikes):
                if candidate > s:
                    pain += put_oi[i] * (candidate - s)
                elif candidate < s:
                    pain += call_oi[i] * (s - candidate)
            if pain < min_pain:
                min_pain = pain
                best_strike = candidate

        self._max_pain[base] = best_strike
        return best_strike

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------

    def get_vix(self) -> float:
        return self._vix

    def get_max_pain(self, base: str) -> float | None:
        return self._max_pain.get(base)
